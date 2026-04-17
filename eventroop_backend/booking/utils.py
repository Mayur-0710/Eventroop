from decimal import Decimal
from dateutil.relativedelta import relativedelta
from datetime import date, time, datetime
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.db.models import QuerySet

from .constants import BookingStatus,PeriodChoices
from .models import PrimaryOrder, SecondaryOrder, TernaryOrder 


class DateParser:
    """Centralized date parsing for DAILY and HOURLY packages."""
    
    @staticmethod
    def parse_dates(period_type: str, raw_dates):
        """
        Parse and validate dates for DAILY and HOURLY packages.
        
        Args:
            period_type: PeriodChoices.DAILY or PeriodChoices.HOURLY
            raw_dates: List (DAILY) or Dict (HOURLY) of date strings
            
        Returns:
            Parsed dates in appropriate format or raises ValidationError
            
        Raises:
            ValidationError: If format is invalid for period type
        """
        
        if period_type == PeriodChoices.DAILY:
            return DateParser._parse_daily(raw_dates)
        elif period_type == PeriodChoices.HOURLY:
            return DateParser._parse_hourly(raw_dates)
        else:
            raise ValidationError(
                {"package": f"Unsupported period type: {period_type}"}
            )
    
    @staticmethod
    def _parse_daily(raw_dates) -> list:
        """Parse DAILY package dates (list of ISO date strings)."""
        if not isinstance(raw_dates, list):
            raise ValidationError(
                {"dates": "For DAILY package, 'dates' must be a list of ISO date strings."}
            )
        
        try:
            return [date.fromisoformat(d) for d in raw_dates]
        except ValueError:
            raise ValidationError(
                {"dates": "Invalid date format. Use YYYY-MM-DD."}
            )
    
    @staticmethod
    def _parse_hourly(raw_dates) -> dict:
        """Parse HOURLY package dates (dict with date keys and time slot values)."""
        if not isinstance(raw_dates, dict):
            raise ValidationError(
                {"dates": "For HOURLY package, 'dates' must be a dictionary."}
            )
        
        try:
            return {
                date.fromisoformat(d): [time.fromisoformat(t) for t in slots]
                for d, slots in raw_dates.items()
            }
        except ValueError:
            raise ValidationError(
                {"dates": "Invalid date or time format. Use YYYY-MM-DD and HH:MM:SS."}
            )
    
    @staticmethod
    def extract_datetime_bounds(period_type: str, parsed_dates) -> tuple:
        """
        Extract start_datetime and end_datetime from parsed dates.
        
        Returns:
            (start_datetime, end_datetime) as timezone-aware datetimes
        """
        if period_type == PeriodChoices.DAILY:
            # parsed_dates is a list of date objects
            min_date = min(parsed_dates)
            max_date = max(parsed_dates)
            start_dt = timezone.make_aware(datetime.combine(min_date, time.min))
            end_dt = timezone.make_aware(datetime.combine(max_date, time.max))
        
        elif period_type == PeriodChoices.HOURLY:
            # parsed_dates is a dict with date keys
            all_dates = list(parsed_dates.keys())
            min_date = min(all_dates)
            max_date = max(all_dates)
            start_dt = timezone.make_aware(datetime.combine(min_date, time.min))
            end_dt = timezone.make_aware(datetime.combine(max_date, time.max))
        
        else:
            raise ValueError(f"Unsupported period type: {period_type}")
        
        return start_dt, end_dt


class OrderQuerySet(QuerySet):
    """Custom QuerySet for PrimaryOrder to reduce duplication."""
    
    def with_related(self):
        """Optimized query with all related objects."""
        return self.select_related(
            'patient', 'venue', 'service', 'package', 'user'
        ).prefetch_related('secondary_orders__ternary_orders')
    
    def exclude_status(self, statuses: list):
        """Exclude orders with given statuses."""
        return self.exclude(status__in=statuses)
    
    def customer_visible(self, user):
        """Filter orders visible to a customer user."""
        from django.db.models import Q
        if user.is_customer:
            return self.filter(
                Q(patient__registered_by=user) | Q(user=user)
            )
        return self
    
    def filter_by_status(self, statuses: list):
        """Filter orders with given statuses."""
        return self.filter(status__in=statuses)
    
    def active(self):
        """Get non-LOBBY and non-HOLD orders (default for OrderViewSet)."""
        return self.exclude_status([BookingStatus.LOBBY, BookingStatus.HOLD])
    
    def pending(self):
        """Get LOBBY and HOLD orders (for LobbyOrderViewSet)."""
        return self.filter_by_status([BookingStatus.LOBBY, BookingStatus.HOLD])
    
    def by_timeframe(self, timeframe: str):
        """Filter by timeframe: 'ongoing', 'upcoming', or 'past'."""
        now = timezone.now()
        
        if timeframe == 'ongoing':
            return self.filter(start_datetime__lte=now, end_datetime__gte=now)
        elif timeframe == 'upcoming':
            return self.filter(start_datetime__gt=now)
        elif timeframe == 'past':
            return self.filter(end_datetime__lt=now)
        else:
            return self


class SecondaryOrderHelper:
    """Helper methods for SecondaryOrder operations."""
    
    @staticmethod
    def get_matching_secondary_order(
        primary_order: PrimaryOrder,
        start_dt: datetime,
        end_dt: datetime
    ) -> SecondaryOrder:
        """
        Find a SecondaryOrder that contains the given datetime range.
        
        Args:
            primary_order: The parent PrimaryOrder
            start_dt: Start datetime for the service
            end_dt: End datetime for the service
            
        Returns:
            SecondaryOrder matching the criteria
            
        Raises:
            SecondaryOrder.DoesNotExist: If no matching secondary order found
        """
        try:
            return primary_order.secondary_orders.get(
                start_datetime__lte=start_dt,
                end_datetime__gte=end_dt,
            )
        except SecondaryOrder.DoesNotExist:
            raise SecondaryOrder.DoesNotExist(
                f"No secondary order found for {start_dt.year}-{start_dt.month:02d}. "
                "Ensure the service date falls within the booking range."
            )
    
    @staticmethod
    def cascade_status_change(
        primary_order: PrimaryOrder,
        new_status: str,
        specific_secondary_id: int = None,
        specific_ternary_id: int = None
    ) -> None:
        """
        Cascade status change to child orders with proper logic.
        
        Rules:
        - Cancelling primary → cancel all children
        - Changing primary status → change all children
        - Changing secondary status → change its ternary orders
        - Changing ternary status → no cascade
        
        Args:
            primary_order: The root PrimaryOrder
            new_status: New status to apply
            specific_secondary_id: If set, only affect this secondary (and its ternaries)
            specific_ternary_id: If set, only affect this ternary (no cascade)
        """
        secondary_ids = primary_order.secondary_orders.values_list('id', flat=True)
        
        if new_status == BookingStatus.CANCELLED and not specific_secondary_id and not specific_ternary_id:
            # Primary cancelled → cascade everywhere
            SecondaryOrder.objects.filter(id__in=secondary_ids).update(status=new_status)
            TernaryOrder.objects.filter(secondary_order_id__in=secondary_ids).update(status=new_status)
        
        elif specific_ternary_id:
            # Only ternary change - no cascade
            pass  # Already handled in caller
        
        elif specific_secondary_id:
            # Secondary change → cascade to its ternaries only
            TernaryOrder.objects.filter(
                secondary_order_id=specific_secondary_id
            ).update(status=new_status)
        
        else:
            # Primary change (non-cancel) → cascade to all children
            SecondaryOrder.objects.filter(id__in=secondary_ids).update(status=new_status)
            TernaryOrder.objects.filter(secondary_order_id__in=secondary_ids).update(status=new_status)


class PermissionHelper:
    """Centralized permission checks for order operations."""
    
    @staticmethod
    def can_modify_order(user, primary_order: PrimaryOrder) -> bool:
        """
        Check if user can modify the given order.
        
        Rules:
        - Superuser: always can
        - Order creator: can modify own orders
        - Owner: can modify orders from their registered patients
        
        Args:
            user: The user attempting modification
            primary_order: The order being modified
            
        Returns:
            True if user has permission, False otherwise
        """
        if user.is_superuser:
            return True
        
        if primary_order.user == user:
            return True
        
        if (user.is_owner and 
            hasattr(primary_order.patient, 'registered_by') and
            primary_order.patient.registered_by.hierarchy.owner == user):
            return True
        
        return False