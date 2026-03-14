from decimal import Decimal
from django.utils import timezone
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from .constants import BookingStatus
from decimal import Decimal
from dateutil.relativedelta import relativedelta

def calculate_amount(startdate, enddate, package):
    if not startdate or not enddate:
        raise ValueError("Start date and end date are required")

    if enddate < startdate:
        raise ValueError("End date must be >= start date")

    # DAILY PACKAGE
    if package.period == "DAILY":
        delta = relativedelta(enddate.date(), startdate.date())
        total_days = delta.days + 1   # include end date
        return Decimal(total_days) * package.price

    # HOURLY PACKAGE
    elif package.period == "HOURLY":
        delta = relativedelta(enddate, startdate)

        total_hours = (
            delta.days * 24
            + delta.hours
            + (1 if delta.minutes > 0 or delta.seconds > 0 else 0)
        )

        # If start == end (same time), count as 1 hour
        if total_hours == 0:
            total_hours = 1

        return Decimal(total_hours) * package.price

    else:
        raise ValueError(f"Unsupported package type:{package.period}")
    
def auto_update_status(start_datetime,end_datetime):
    now = timezone.now()
    status =  BookingStatus.DRAFT
    if now < start_datetime:
        status = BookingStatus.YET_TO_START
    elif start_datetime <= now <= end_datetime:
        status = BookingStatus.IN_PROGRESS
    elif now > end_datetime:
        status = BookingStatus.FULFILLED
    return status

def generate_order_id(instance):
    if not instance.id:
        raise ValueError("Instance must be saved before generating order_id")

    # TernaryOrder
    if hasattr(instance, "secondary_order") and instance.secondary_order:
        secondary = instance.secondary_order
        primary = instance.secondary_order.primary_order
        return f"#{primary.id:03}{secondary.id:03}{instance.id:03}"

    # SecondaryOrder
    if hasattr(instance, "primary_order") and instance.primary_order:
        primary = instance.primary_order
        return f"#{primary.id:03}{instance.id:03}000"

    # PrimaryOrder
    return f"#{instance.id:03}000000"