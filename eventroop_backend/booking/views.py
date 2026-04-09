from venue_manager.models import Venue, Service, Resource
from venue_manager.serializers import (
    VenueSerializer,
    ServiceSerializer,
    VenueDropdownSerializer,
    ServiceDropdownSerializer
)
from rest_framework import viewsets, permissions, status,pagination
from .serializers import *
from .models import *
from .filters import EntityFilter
from rest_framework.decorators import action
from rest_framework.response import Response
from django.utils.dateparse import parse_datetime
from datetime import datetime, time, date
from itertools import groupby
from django.db.models import Sum,Count,Q

class PublicVenueViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Single API for:
      - GET /venues/          → list venues with filters
      - GET /venues/<id>/     → venue details
    """
    serializer_class = VenueSerializer
    permission_classes = [permissions.AllowAny]
    
    lookup_field = "pk"
    queryset = Venue.objects.filter(is_deleted=False, is_active=True).order_by("id")

    filterset_fields = {
        "location__city": ["iexact", "icontains"],
        "location__state": ["iexact", "icontains"],
        "capacity": ["gte", "lte", "exact"],
        "price_per_event": ["gte", "lte"],
        "rooms": ["gte", "lte"],
        "floors": ["gte", "lte"],
        "external_decorators_allow": ["iexact"],
        "external_caterers_allow": ["iexact"],
    }

    search_fields = [
        "name",
        "description",
        "location__building_name",
        "location__address_line1",
        "location__address_line2",
        "location__locality",
        "location__city",
        "location__state",
    ]
    @action(detail=False, methods=["get"])
    def venue_dropdown(self,request):
        queryset = self.filter_queryset(self.get_queryset())
        return Response(VenueDropdownSerializer(queryset,many=True).data)

class PublicServiceViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Single API for:
      - GET /services/          → list services with filters
      - GET /services/<id>/     → service details
    """
    serializer_class = ServiceSerializer
    permission_classes = [permissions.AllowAny]
    
    filterset_class = EntityFilter
    lookup_field = "pk"

    queryset = Service.objects.filter(is_deleted=False, is_active=True).order_by("id")

    filterset_fields = {
        "city": ["iexact", "icontains"],
    }

    search_fields = [
        "venue__name",
        "name",
        "description",
        "address",
        "city",
        "contact",
        "website",
        "tags",
        "quick_info"
    ]
    @action(detail=False, methods=["get"])
    def service_dropdown(self,request):
        queryset = self.filter_queryset(self.get_queryset())
        return Response(ServiceDropdownSerializer(queryset,many=True).data)

class ContactBookingViewSet(viewsets.ModelViewSet):
    queryset = ContactBooking.objects.select_related(
        'patient', 'booked_by', 'service'
    ).all()
    serializer_class = ContactBookingSerializer
    pagination_class = pagination.CursorPagination

    def perform_create(self, serializer):
        serializer.save(booked_by=self.request.user)

class PatientViewSet(viewsets.ModelViewSet):
    queryset = Patient.objects.all()
    serializer_class = PatientSerializer
    
    # Simple filtering
    filterset_fields = [
        "gender",
        "blood_group",
        "payment_mode",
        "registered_by",
        "id_proof",
        "registration_date",
    ]

    # Search 
    search_fields = [
        "first_name",
        "last_name",
        "email",
        "phone",
        "patient_id",
    ]

    # Ordering
    ordering_fields = [
        "registration_date",
        "first_name",
        "age",
        "registration_fee",
    ]

    ordering = ['-registration_date']

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser or user.is_owner:
            return Patient.objects.all()
        
        # Manager/Staff/customer → only their own patients
        return Patient.objects.filter(registered_by=user)
    
    def perform_create(self, serializer):
        """
        Set registered_by = request.user automatically
        """
        serializer.save(registered_by=self.request.user)
    
    @action(detail=False, methods=["get"])
    def patient_dropdown(self, request):
        queryset = self.filter_queryset(self.get_queryset())

        # Select only required fields (IMPORTANT for performance)
        queryset = queryset.only("id", "first_name", "last_name")

        # Build lightweight response
        data = [
            {
                "id": obj.id,
                "name": obj.get_full_name()
            }
            for obj in queryset
        ]

        return Response(data,status=status.HTTP_200_OK)
    
class LocationViewSet(viewsets.ModelViewSet):
    serializer_class = LocationSerializer

    filterset_fields = ["location_type","user__first_name","user__email","user__mobile_number", "city", "state"]
    search_fields = [
        "user__first_name",
        "user__email",
        "user__mobile_number",
        "building_name",
        "address_line1",
        "locality",
        "city",
        "state",
        "postal_code",
    ]
    
    def get_queryset(self):
        user = self.request.user

        # Admin → see all locations
        if user.is_superuser:
            qs = Location.objects.all()
        else:
            qs = Location.objects.filter(user=user)

        return qs.order_by("-id")
    
    def perform_create(self, serializer):
        # Automatically set the user to the requesting user
        serializer.save(user=self.request.user)

    def perform_update(self, serializer):
        # Ensure user cannot be changed on update
        serializer.save(user=self.request.user)

class PackageViewSet(viewsets.ModelViewSet):
    queryset = Package.objects.all()
    serializer_class = PackageSerializer
    filterset_fields = ['package_type', 'is_active', 'owner', 'content_type__model']
    search_fields = ['name', 'description']
    ordering_fields = ['created_at', 'price', 'name']
    ordering = ['-created_at']

    def get_serializer_class(self):
        if self.action == 'create':
            return PackageCreateSerializer
        return PackageSerializer

    def get_queryset(self):
        user = self.request.user
        # Owners can only see their own packages, staff can see all
        if user.is_superuser:
            return Package.objects.all()
        elif user.is_owner:
            return Package.objects.filter(owner=user)
        elif user.is_vsre_staff:
            return Package.objects.filter(owner=user.hierarchy.owner)
        return Package.objects.none()

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    def perform_update(self, serializer):
        if not self.request.user.is_owner:
            return Response(
                {'detail': 'You do not have permission to update this package.'},
                status=status.HTTP_403_FORBIDDEN
            )
        serializer.save()

    def perform_destroy(self, instance):
        if not self.request.user.is_owner:
            return Response(
                {'detail': 'You do not have permission to delete this package.'},
                status=status.HTTP_403_FORBIDDEN
            )
        instance.delete()

    @action(detail=False, methods=['get'])
    def by_type(self, request):
        """Filter packages by type"""
        pkg_type = request.query_params.get('type')
        if not pkg_type:
            return Response(
                {'error': 'type parameter is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        packages = self.get_queryset().filter(package_type=pkg_type)
        serializer = self.get_serializer(packages, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=["get"])
    def by_belongs_to(self, request):
        """
        Examples:

        /booking/packages/by_belongs_to/?entity=venue
            -> returns all venues (id, name)

        /booking/packages/by_belongs_to/?entity=venue&id=3
            -> returns packages of venue 3
        """

        content_type_name = request.query_params.get("entity")
        object_id = request.query_params.get("id")

        if not content_type_name:
            return Response(
                {"error": "content_type is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ENTITY_MAP = {
            "venue": Venue,
            "service": Service,
            "resource": Resource,
        }

        content_type_name = content_type_name.lower()

        if content_type_name not in ENTITY_MAP:
            return Response(
                {"error": "content_type must be venue, service, or resource"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        Model = ENTITY_MAP[content_type_name]

        # CASE 1 → Only content_type → return entities (Service + Venue)
        if not object_id:
            queryset = Model.objects.filter(owner=request.user, is_active=True)

            data = queryset.values("id", "name")

            return Response(data)

        # CASE 2 → content_type + object_id → return packages
        try:
            obj = Model.objects.get(
                id=object_id,
                is_active=True,
            )
        except Model.DoesNotExist:
            return Response(
                {"error": f"{content_type_name.title()} not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # GenericRelation
        packages = obj.packages.all()

        serializer = PackageSerializer(packages, many=True)
        return Response(serializer.data)

class OrderViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing primary orders.

    Handles:
    - Creating venue bookings with full-range OR specific date/slot selection
    - Adding service bookings as TernaryOrders
    - Cancelling orders (cascades to Secondary & Ternary)
    - Rescheduling orders (regenerates sub-orders)
    """

    search_fields = ['patient__first_name', 'patient__last_name', 'booking_entity', 'status']
    filterset_fields = {
        'patient': ['exact'],
        'package': ['exact'],
        'start_datetime': ['gte'],
        'end_datetime': ['lte'],
        'booking_type': ['exact'],
        'status': ['exact'],
    }
    ordering_fields = ['user', 'patient', 'created_at', 'start_datetime', 'end_datetime']
    ordering = ['-created_at']

    # ── Queryset ───────────────────────────────────────────────────────────────
    def get_queryset(self):
        user = self.request.user
        queryset = PrimaryOrder.objects.select_related(
            'patient', 'venue', 'service', 'package', 'user'
        ).prefetch_related('secondary_orders__ternary_orders')
        
        if user.is_customer:
            queryset = queryset.filter(Q(patient__registered_by=user)|Q(user=user))

        now = timezone.now()

        if self.request.query_params.get('ongoing'):
            queryset = queryset.filter(start_datetime__lte=now, end_datetime__gte=now)

        if self.request.query_params.get('upcoming'):
            queryset = queryset.filter(start_datetime__gt=now)

        if self.request.query_params.get('past_order'):
            queryset = queryset.filter(end_datetime__lt=now)

        service_id = self.request.query_params.get('service_id')
        if service_id:
            queryset = queryset.filter(service=service_id)

        return queryset

    # ── Serializer ─────────────────────────────────────────────────────────────
    def get_serializer_class(self):
        if self.action in ('create','update'):
            return PrimaryOrderCreateSerializer
        return PrimaryOrderSerializer

    # ── Create ─────────────────────────────────────────────────────────────────
    @transaction.atomic
    def create(self, request, *args, **kwargs):
        """
        Create a new PrimaryOrder.

        Two modes based on payload:

        Mode 1 — Full range (start_datetime + end_datetime only):
            SecondaryOrders are auto-generated for every period in the full range.
            {
                "patient": 1,
                "venue": 2,
                "package": 3,
                "start_datetime": "2026-03-01T00:00:00Z",
                "end_datetime": "2026-05-31T23:59:59Z"
            }

        Mode 2 — Specific dates (DAILY package → list, HOURLY package → dict):
            Only the provided dates/slots get SecondaryOrders.
            DAILY:
            {
                "patient": 1,
                "venue": 2,
                "package": 3,
                "dates": ["2026-03-01", "2026-03-05", "2026-03-10"]
            }
            HOURLY:
            {
                "patient": 1,
                "venue": 2,
                "package": 3,
                "dates": {
                    "2026-03-01": ["09:00:00", "10:00:00", "11:00:00"],
                    "2026-03-05": ["14:00:00", "15:00:00"]
                }
            }
        """
        raw_dates = request.data.get('dates')
        print(f"raw_dates:{raw_dates}")
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        serializer.validated_data['user'] = request.user
        
        if raw_dates:
            date_keys = list(raw_dates.keys()) if isinstance(raw_dates, dict) else raw_dates
            parsed_dates = [date.fromisoformat(d) for d in date_keys]
            serializer.validated_data['start_datetime'] = timezone.make_aware(datetime.combine(min(parsed_dates), time.min))
            serializer.validated_data['end_datetime'] = timezone.make_aware(datetime.combine(max(parsed_dates), time.max))
        primary_order = serializer.save()
            
        # ── Decide generation mode ────────────────────────────────────────────
        try:
            if raw_dates:
                # Mode 2: specific dates / slots
                parsed = self.parse_dates(primary_order.package.period,raw_dates)
                primary_order.generate_secondary_from_random_dates(parsed)
            else:
                # Mode 1: full range (start_datetime + end_datetime required by serializer)
                primary_order.generate_secondary_full_range_dates()
        except ValidationError as e:
            return Response(e.message_dict, status=status.HTTP_400_BAD_REQUEST)
        
        response_serializer = PrimaryOrderSerializer(primary_order)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)
    
    # ── update ─────────────────────────────────────────────────────────────────
    @transaction.atomic
    def update(self, request, *args, **kwargs):
        """
        Update PrimaryOrder and regenerate SecondaryOrders if schedule changes.
        """

        instance = self.get_object()

        raw_dates = request.data.get("dates")

        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        old_start = instance.start_datetime
        old_end = instance.end_datetime
        old_package = instance.package

        # Handle date payload like create()
        if raw_dates:
            date_keys = list(raw_dates.keys()) if isinstance(raw_dates, dict) else raw_dates
            parsed_dates = [date.fromisoformat(d) for d in date_keys]

            serializer.validated_data["start_datetime"] = timezone.make_aware(
                datetime.combine(min(parsed_dates), time.min)
            )
            serializer.validated_data["end_datetime"] = timezone.make_aware(
                datetime.combine(max(parsed_dates), time.max)
            )

        primary_order = serializer.save()

        schedule_changed =True

        if schedule_changed:

            # Delete old secondary orders
            primary_order.secondary_orders.all().delete()
            try:
                if raw_dates:
                    parsed = self.parse_dates(primary_order.package.period, raw_dates)
                    primary_order.generate_secondary_from_random_dates(parsed)
                else:
                    primary_order.generate_secondary_full_range_dates()
            except ValidationError as e:
                return Response(e.message_dict, status=status.HTTP_400_BAD_REQUEST)
        response_serializer = PrimaryOrderSerializer(primary_order)

        return Response(response_serializer.data, status=status.HTTP_200_OK)
    
    # ── Add service (TernaryOrder) ──────────────────────────────────────────────
    @action(detail=True, methods=['post'])
    @transaction.atomic
    def add_service(self, request, pk=None):
        """
        Add a service as a TernaryOrder under the appropriate SecondaryOrder.

        The secondary_order is matched by the service's date range (month/year).

        Payload:
        {
            "venue": 13,
            "service": 5,
            "package": 2,
            "start_datetime": "2026-02-05T10:00:00Z",
            "end_datetime": "2026-02-05T11:00:00Z",
            "discount_amount": "100.00",
            "premium_amount": "50.00"
        }
        """
        primary_order = self.get_object()
        user = request.user

        # Permission Check
        if not (user.is_superuser or primary_order.user == user or 
                (user.is_owner and primary_order.patient.registered_by.hierarchy.owner == user)):
             return Response(
                {"detail": "You do not have permission to modify this order."},
                status=status.HTTP_403_FORBIDDEN
            )

        if primary_order.status == BookingStatus.CANCELLED:
            return Response(
                {"message": "Cannot add a service to a cancelled order."},
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer = TernaryOrderCreateSerializer(
            data=request.data,
            context={"primary_order": primary_order}
        )
        serializer.is_valid(raise_exception=True)

        start_dt = serializer.validated_data['start_datetime']
        end_dt   = serializer.validated_data['end_datetime']

        # Resolve the matching SecondaryOrder of start_dt
        try:
           secondary_order = primary_order.secondary_orders.filter(
                start_datetime__lte=start_dt,
                end_datetime__gte=end_dt,
            ).first()
        except SecondaryOrder.DoesNotExist:
            return Response(
                {
                    "message": (
                        f"No secondary order found for "
                        f"{start_dt.year}-{start_dt.month:02d}. "
                        "Ensure the service date falls within the booking range."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        ternary_order = serializer.save(secondary_order=secondary_order)

        # Recalculate subtotals up the chain
        secondary_order.recalculate_subtotal()
        primary_order.recalculate_total()

        response_serializer = TernaryOrderSerializer(ternary_order)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)

    # ── Reschedule ─────────────────────────────────────────────────────────────
    @action(detail=True, methods=["post"])
    @transaction.atomic
    def reschedule_order(self, request, pk=None):

        primary_order = self.get_object()

        new_package_id   = request.data.get("package")
        discount_amount  = Decimal(request.data.get("discount_amount", "0"))
        premium_amount   = Decimal(request.data.get("premium_amount", "0"))
        raw_dates        = request.data.get("dates")

        # Determine package & period type        
        package = primary_order.package

        if new_package_id:
            from .models import Package
            package = Package.objects.get(id=new_package_id)

        period_type = package.period

        try:
            # MODE 2 — Specific Dates / Slots
            if raw_dates:
                try:
                    parsed = self.parse_dates(period_type, raw_dates)

                    if isinstance(parsed, list):  # DAILY
                        new_start = timezone.make_aware(
                            datetime.combine(min(parsed), time.min)
                        )
                        new_end = timezone.make_aware(
                            datetime.combine(max(parsed), time.max)
                        )
                    else:  # HOURLY
                        all_dates = list(parsed.keys())
                        new_start = timezone.make_aware(
                            datetime.combine(min(all_dates), time.min)
                        )
                        new_end = timezone.make_aware(
                            datetime.combine(max(all_dates), time.max)
                        )
                except ValidationError as e:
                    return Response(e.message_dict, status=status.HTTP_400_BAD_REQUEST)

                primary_order.reschedule(
                    new_start,
                    new_end,
                    new_package_id,
                    discount_amount,
                    premium_amount,
                )

                # Delete auto-generated ones
                primary_order.secondary_orders.all().delete()

                # Generate specific ones
                primary_order.generate_secondary_from_random_dates(parsed)

            # MODE 1 — Full Range
            else:
                new_start_raw = request.data.get("start_datetime")
                new_end_raw   = request.data.get("end_datetime")

                if not new_start_raw or not new_end_raw:
                    raise ValidationError(
                        {"detail": "'start_datetime' and 'end_datetime' are required."}
                    )

                new_start = parse_datetime(new_start_raw)
                new_end   = parse_datetime(new_end_raw)

                if not new_start or not new_end:
                    raise ValidationError(
                        {"detail": "Invalid datetime format."}
                    )

                if timezone.is_naive(new_start):
                    new_start = timezone.make_aware(new_start)

                if timezone.is_naive(new_end):
                    new_end = timezone.make_aware(new_end)

                primary_order.reschedule(
                    new_start,
                    new_end,
                    new_package_id,
                    discount_amount,
                    premium_amount,
                )

        except ValidationError:
            raise  # Let DRF handle structured error

        except Exception as e:
            return Response(
                {"detail": f"Invalid input: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = PrimaryOrderSerializer(primary_order)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    @transaction.atomic
    def reschedule_service(self, request, pk=None):
        """
        Reschedule a single TernaryOrder and recalculate parent totals.

        Payload:
        {
            "ternary_order_id": 5,
            "start_datetime": "2026-02-10T10:00:00Z",
            "end_datetime": "2026-02-10T12:00:00Z",
            "package": 2,               # optional
            "discount_amount": 0.0,     # optional
            "premium_amount": 0.0       # optional
        }
        """
        primary_order = self.get_object()

        ternary_order_id = request.data.get('ternary_order_id')
        new_start        = request.data.get('start_datetime')
        new_end          = request.data.get('end_datetime')
        new_package      = request.data.get('package')
        discount_amount  = request.data.get('discount_amount', Decimal('0'))
        premium_amount   = request.data.get('premium_amount', Decimal('0'))

        if not new_start or not new_end:
            return Response(
                {"message": "'start_datetime' and 'end_datetime' are required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            ternary_order = TernaryOrder.objects.get(
                id=ternary_order_id,
                secondary_order__primary_order=primary_order
            )
        except TernaryOrder.DoesNotExist:
            return Response(
                {"message": "Service (TernaryOrder) not found under this order."},
                status=status.HTTP_404_NOT_FOUND
            )

        try:
            new_start_dt = parse_datetime(new_start)
            new_end_dt   = parse_datetime(new_end)

            if not new_start_dt or not new_end_dt:
                raise ValueError("Invalid datetime format.")

            with transaction.atomic():
                ternary_order.start_datetime = new_start_dt
                ternary_order.end_datetime   = new_end_dt

                if new_package:
                    ternary_order.package_id = new_package
                    ternary_order.status  = BookingStatus.MODIFIED
                else:
                    ternary_order.status  = BookingStatus.RESCHEDULED
                target.status_locked = True

                if discount_amount is not None:
                    ternary_order.discount_amount = discount_amount
                if premium_amount is not None:
                    ternary_order.premium_amount  = premium_amount
                
                ternary_order.save()

                ternary_order.secondary_order.recalculate_subtotal()
                primary_order.recalculate_total()

        except ValidationError as e:
            return Response({"message": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except (ValueError, TypeError) as e:
            return Response({"message": f"Invalid input: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)

        serializer = TernaryOrderSerializer(ternary_order)
        return Response(serializer.data)

    # ── Status change ──────────────────────────────────────────────────────────

    @action(detail=True, methods=['patch'])
    def change_status(self, request, pk=None):
        """
        Manually change the status of a PrimaryOrder (and optionally a child TernaryOrder).

        Payload:
        {
            "status": "CONFIRMED",
            "secondary_order_id": 3,  # optional — targets a specific SecondaryOrder
            "ternary_order_id": 5     # optional — targets a specific TernaryOrder
        }
        """

        primary_order      = self.get_object()
        secondary_order_id = request.data.get('secondary_order_id')
        ternary_order_id   = request.data.get('ternary_order_id')
        new_status         = request.data.get('status')

        if not new_status:
            return Response({"error": "Status is required."}, status=status.HTTP_400_BAD_REQUEST)

        if new_status not in BookingStatus.values:
            return Response({"error": "Invalid status."}, status=status.HTTP_400_BAD_REQUEST)

        target = primary_order

        if secondary_order_id:
            try:
                target = SecondaryOrder.objects.get(
                    id=secondary_order_id,
                    primary_order=primary_order
                )
            except SecondaryOrder.DoesNotExist:
                return Response({"error": "Invalid secondary_order_id."}, status=status.HTTP_400_BAD_REQUEST)

        if ternary_order_id:
            try:
                target = TernaryOrder.objects.get(
                    id=ternary_order_id,
                    secondary_order__primary_order=primary_order
                )
            except TernaryOrder.DoesNotExist:
                return Response({"error": "Invalid ternary_order_id."}, status=status.HTTP_400_BAD_REQUEST)

        # available_statuses = MANUAL_STATUS_TRANSITIONS.get(target.status, [])
        # if new_status not in available_statuses:
        #     return Response(
        #         {"error": f"Cannot transition from '{target.status}' to '{new_status}'."},
        #         status=status.HTTP_400_BAD_REQUEST
        #     )

        with transaction.atomic():
            target.status = new_status
            target.status_locked = True
            target.save(update_fields=['status','status_locked'])

            # If primary order is canceled, force cascade to ALL secondaries and ternaries
            if target == primary_order and new_status == BookingStatus.CANCELLED:
                secondary_ids = primary_order.secondary_orders.values_list('id', flat=True)
                SecondaryOrder.objects.filter(id__in=secondary_ids).update(status=new_status)
                TernaryOrder.objects.filter(secondary_order_id__in=secondary_ids).update(status=new_status)

            elif secondary_order_id and not ternary_order_id:
                # Cascade to this secondary's ternary orders only
                target.ternary_orders.all().update(status=new_status)

            elif not ternary_order_id and not secondary_order_id:
                # Primary status change (non-cancel): cascade to all children
                secondary_ids = primary_order.secondary_orders.values_list('id', flat=True)
                SecondaryOrder.objects.filter(id__in=secondary_ids).update(status=new_status)
                TernaryOrder.objects.filter(secondary_order_id__in=secondary_ids).update(status=new_status)

        return Response({
            "message": "Status updated successfully.",
            "order_id": target.id,
            "new_status": new_status
        }, status=status.HTTP_200_OK)

    # ── Info endpoints ─────────────────────────────────────────────────────────
    @action(detail=False, methods=['get'])
    def by_venue(self, request):
        """List all venue PrimaryOrders with nested secondary/ternary data."""
        queryset   = self.get_queryset().filter(booking_entity=BookingEntity.VENUE)
        serializer = PrimaryOrderSerializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def by_service(self, request):
        """List all service PrimaryOrders."""
        queryset   = self.get_queryset().filter(booking_entity=BookingEntity.SERVICE)
        serializer = PrimaryOrderSerializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    def order_info(self, request, pk=None):
        """Full breakdown of a PrimaryOrder including all secondary and ternary orders."""
        primary_order = self.get_object()
        serializer    = PrimaryOrderSerializer(primary_order)
        return Response(serializer.data)

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def parse_dates(period_type, raw_dates):
        """
        Parse and validate dates for DAILY and HOURLY packages.
        Returns parsed data or raises ValidationError.
        """
        
        if period_type == PeriodChoices.DAILY:

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

        elif period_type == PeriodChoices.HOURLY:

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
        else:
            raise ValidationError(
                {"package": f"Unsupported period type: {period_type}"}
            )

class TotalInvoiceViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing invoices.
        
    Handles:
    - Manual invoice creation for specific periods
    - Retrieving invoice details and payments
    - Manual recalculation endpoints
    """
    queryset = TotalInvoice.objects.select_related(
        'secondary_order__primary_order__venue__location',
        'secondary_order__primary_order__service',
        'secondary_order__primary_order__package',
        'ternary_order__venue__location',
        'ternary_order__service',
        'ternary_order__package',
        'user',
        'patient',
    ).prefetch_related('payments')

    serializer_class = TotalInvoiceSerializer
    search_fields = ['invoice_number', 'patient__first_name', 'patient__last_name', 'status']
    filterset_fields = {
        'patient': ['exact'],
        'period_start': ['gte'],
        'period_end': ['lte'],
        'secondary_order__primary_order__booking_type': ['exact'],
        'ternary_order__booking_type': ['exact'],
        'status': ['exact'],
    }

    ordering_fields = [
        'user', 'patient', 'created_at', 'period_start', 'period_end',
        'issued_date', 'status', 'total_amount'
    ]
    ordering = ['-created_at']
    
    
    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user

        # Filter by customer
        if user.is_customer:
            queryset = queryset.filter(Q(patient__registered_by=user)|Q(user=user))
    
        return queryset
    
    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset()).order_by('user_id', 'patient_id')
        
        grouped_data = []
        
        for (user_id, patient_id), invoices_group in groupby(
            queryset,
            key=lambda x: (x.user_id, x.patient_id)
        ):
            invoices_list = list(invoices_group)
            
            if not invoices_list:
                continue
            
            first_invoice = invoices_list[0]
            user = first_invoice.user
            patient = first_invoice.patient
            
            total_invoice_amount = sum(inv.total_amount for inv in invoices_list)
            total_paid = sum(inv.paid_amount for inv in invoices_list)
            total_balance = total_invoice_amount - total_paid
            
            serializer = self.serializer_class(invoices_list, many=True)
            invoices_array = serializer.data
            
            grouped_data.append({
                "user_id": user.id,
                "user_name": user.get_full_name(),
                "patient_id": patient.id,
                "patient_name": patient.get_full_name(),
                "total_invoice_amount": str(total_invoice_amount),
                "total_paid": str(total_paid),
                "total_balance": str(total_balance),
                "invoices": invoices_array
            })
        
        page = self.paginate_queryset(grouped_data)
        
        if page is not None:
            return self.get_paginated_response(page)
        
        return Response(grouped_data)
    
    @action(detail=True, methods=['get'])
    def recalculate(self, request, pk=None):
        """
        Manually recalculate invoice totals based on current bookings and payments.
        """
        invoice = self.get_object()
        invoice.recalculate_totals()
        
        serializer = TotalInvoiceSerializer(invoice)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def add_payment(self, request, pk=None):
        """
        Add a payment to an invoice.
        Invoice status is automatically recalculated.
        """
        invoice = self.get_object()
        
        serializer = PaymentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        payment = Payment.objects.create(
            invoice=invoice,
            patient=invoice.patient,
            **serializer.validated_data
        )
        
        # Payment.save() automatically calls invoice.recalculate_payments()
        
        payment_serializer = PaymentSerializer(payment)
        return Response(payment_serializer.data, status=status.HTTP_201_CREATED)
    
    @action(detail=False, methods=['get'])
    def summary(self, request):
        """Get invoice summary statistics for the current user."""
        queryset = self.filter_queryset(self.get_queryset())
        total_stats = queryset.aggregate(
            total_invoices=Count('id'),
            total_amount=Sum('total_amount'),
            paid_amount=Sum('paid_amount'),
            remaining_amount=Sum('remaining_amount'),
            unpaid_count=Count('id', filter=Q(status=InvoiceStatus.UNPAID)),
            partially_paid_count=Count('id', filter=Q(status=InvoiceStatus.PARTIALLY_PAID)),
            paid_count=Count('id', filter=Q(status=InvoiceStatus.PAID))
        )
        
        serializer = InvoiceSummarySerializer(total_stats)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def overdue(self, request):
        """Get all overdue invoices (due_date passed and status not PAID)."""
        today = timezone.now().date()
        overdue_invoices = self.get_queryset().filter(
            due_date__lt=today,
            status__in=['UNPAID', 'PARTIALLY_PAID']
        )
        
        serializer = self.serializer_class(overdue_invoices, many=True)
        return Response(serializer.data)
    
class PaymentViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing payments.
        
    Handles:
    - Recording payments
    - Verifying/unverifying payments
    - Tracking payment methods
    """
    filterset_fields = ['invoice_id', 'is_verified', 'method']  
    ordering_fields = ['created_at', 'amount', 'paid_date']
    ordering = ['-created_at']
    
    def get_queryset(self):
        """Get payments for invoices belonging to current user"""
        queryset = Payment.objects.select_related('invoice', 'patient')
        user = self.request.user
        
        if user.is_customer:
            queryset = queryset.filter(Q(patient__registered_by=user)|Q(user=user))
    
        return queryset
    
    def get_serializer_class(self):
        """Return appropriate serializer based on action"""
        if self.action == 'create':
            return PaymentCreateSerializer
        return PaymentSerializer
    
    @transaction.atomic
    def create(self, request, *args, **kwargs):
        """
        Create a new payment.
        Invoice status is automatically recalculated.
        """
        invoice_id = request.data.get('invoice_id')
        
        try:
            invoice = TotalInvoice.objects.get(
                id=invoice_id
            )
        except TotalInvoice.DoesNotExist:
            return Response(
                {'error': 'Invoice not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        serializer = PaymentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        payment = Payment.objects.create(
            invoice=invoice,
            patient=invoice.patient,
            **serializer.validated_data
        )
                
        output_serializer = PaymentSerializer(payment)
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)
    
    @action(detail=True, methods=['post'])
    def verify(self, request, pk=None):
        """
        Mark a payment as verified.
        Invoice status is automatically recalculated.
        """
        payment = self.get_object()
        
        if not payment.invoice:
            return Response(
                {'error': 'Payment has no associated invoice'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Model's verify method handles everything
        success = payment.verify()
        
        if not success:
            return Response(
                {'message': 'Payment is already verified'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        serializer = PaymentSerializer(payment)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def unverify(self, request, pk=None):
        """
        Mark a payment as unverified.
        Invoice status is automatically recalculated.
        """
        payment = self.get_object()
        
        if not payment.invoice:
            return Response(
                {'error': 'Payment has no associated invoice'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Model's unverify method handles everything
        success = payment.unverify()
        
        if not success:
            return Response(
                {'message': 'Payment is not verified'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        serializer = PaymentSerializer(payment)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def pending_verification(self, request):
        """Get all payments pending verification."""
        pending_payments = self.get_queryset().filter(is_verified=False)
        serializer = PaymentSerializer(pending_payments, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def verified(self, request):
        """Get all verified payments."""
        verified_payments = self.get_queryset().filter(is_verified=True)
        serializer = PaymentSerializer(verified_payments, many=True)
        return Response(serializer.data)
    
    