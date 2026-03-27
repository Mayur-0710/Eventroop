from rest_framework import serializers
from django.db import transaction
from .models import *
from .constants import *
from django.contrib.contenttypes.models import ContentType

class LocationSerializer(serializers.ModelSerializer):
    full_address = serializers.SerializerMethodField(read_only=True)
    user_name = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Location
        fields = [
            "id",
            "user",
            "user_name",
            "location_type",
            "building_name",
            "address_line1",
            "address_line2",
            "locality",
            "city",
            "state",
            "postal_code",
            "full_address",
        ]
        read_only_fields = ["user"]

    def get_full_address(self, obj):
        return obj.full_address()

    def get_user_name(self, obj):
        return obj.user.get_full_name() if obj.user else None

class PatientSerializer(serializers.ModelSerializer):
    name_registered_by = serializers.CharField(
        source="registered_by.get_full_name", read_only=True
    )
    full_name = serializers.CharField(source="get_full_name", read_only=True)

    class Meta:
        model = Patient
        fields = "__all__"
        read_only_fields = [
            "id",
            "patient_id",
            "name_registered_by",
            "registered_by",
            "registration_date",
            
        ]

class PatientMiniSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source="get_full_name", read_only=True)

    class Meta:
        model = Patient
        fields = [
            "id",
            "patient_id",
            "name",
            "email",
            "phone",
            "age",
            "emergency_contact",
            "emergency_phone",
        ]

class PackageCreateSerializer(serializers.ModelSerializer):
    belongs_to_type = serializers.CharField(write_only=True)

    class Meta:
        model = Package
        fields = [
            "name",
            "description",
            "package_type",
            "period",
            "price",
            "is_active",
            "object_id",
            "belongs_to_type",
        ]
        read_only_fields=[
            "package_type"
        ]


    def validate(self, attrs):
        model_name = attrs.pop("belongs_to_type", None)

        if not model_name:
            raise serializers.ValidationError(
                {"belongs_to_type": "This field is required."}
            )

        try:
            content_type = ContentType.objects.get(
                model=model_name.lower()
            )
            attrs["content_type"] = content_type
        except ContentType.DoesNotExist:
            raise serializers.ValidationError(
                {"belongs_to_type": "Invalid model name."}
            )

        return attrs

class PackageSerializer(serializers.ModelSerializer):
    owner_name = serializers.CharField(
        source="owner.get_full_name",
        read_only=True
    )
    belongs_to_type = serializers.CharField(
        source="content_type.model",
        read_only=True
    )

    class Meta:
        model = Package
        fields = [
            "id",
            "owner",
            "owner_name",
            "name",
            "description",
            "package_type",
            "period",
            "price",
            "is_active",
            "object_id",
            "belongs_to_type",
        ]

        read_only_fields = [
            "id",
            "owner",
            "object_id",
            "belongs_to_type",
        ]

class TernaryOrderCreateSerializer(serializers.ModelSerializer):
    """
    Create a TernaryOrder (service) under a SecondaryOrder.
    `secondary_order` is injected by the ViewSet via save().
    `primary_order` context is passed for date-range validation.
    """

    class Meta:
        model = TernaryOrder
        fields = [
            'venue',
            'service',
            'package',
            'client_address',
            'start_datetime',
            'end_datetime',
            'discount_amount',
            'premium_amount',
        ]
        extra_kwargs = {
            'venue': {'required': True},
            'service': {'required': True},
            'package': {'required': True},
            'client_address':  {'required': False},

        }

    def validate(self, attrs):
        primary_order = self.context.get('primary_order')

        if not primary_order:
            raise serializers.ValidationError(
                {"primary_order": "Primary order context is missing."}
            )

        start_datetime = attrs.get('start_datetime')
        end_datetime   = attrs.get('end_datetime')

        if start_datetime and end_datetime:
            if start_datetime >= end_datetime:
                raise serializers.ValidationError(
                    {"start_datetime": "Start datetime must be before end datetime."}
                )

            if (
                start_datetime < primary_order.start_datetime
                or end_datetime > primary_order.end_datetime
            ):
                raise serializers.ValidationError(
                    {
                        "start_datetime": (
                            "Service dates must fall within the primary order range "
                            f"({primary_order.start_datetime} - {primary_order.end_datetime})."
                        )
                    }
                )

        return attrs

class TernaryOrderSerializer(serializers.ModelSerializer):
    """Read serializer for a single TernaryOrder (service line item)."""

    venue_name = serializers.CharField(
        source='venue.name',
        read_only=True,
        allow_null=True,
    )
    service_name = serializers.CharField(
        source='service.name',
        read_only=True,
        allow_null=True,
    )
    package_name = serializers.CharField(
        source='package.name',
        read_only=True,
        allow_null=True,
    )
    location_locality = serializers.CharField(
        source='venue.location.locality',
        read_only=True,
        allow_null=True,
    )

    class Meta:
        model = TernaryOrder
        fields = [
            'id',
            'order_id',
            'booking_entity',
            'booking_type',
            'venue',
            'venue_name',
            'service',
            'service_name',
            'package',
            'package_name',
            'location_locality',
            'client_address',
            'start_datetime',
            'end_datetime',
            'discount_amount',
            'premium_amount',
            'subtotal',
            'status',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'order_id', 'subtotal', 'created_at', 'updated_at']
         

class SecondaryOrderSerializer(serializers.ModelSerializer):
    """Read serializer for a SecondaryOrder (one period/month slot)."""

    ternary_orders = TernaryOrderSerializer(many=True, read_only=True)
    service_name = serializers.CharField(
        source='primary_order.service.name',
        read_only=True,
        allow_null=True,
    )
    package_name = serializers.CharField(
        source='primary_order.package.name',
        read_only=True,
        allow_null=True,
    )
    location_locality = serializers.SerializerMethodField()


    class Meta:
        model = SecondaryOrder
        fields = [
            'id',
            'order_id',
            'service_name',
            'package_name',
            'location_locality',
            'start_datetime',
            'end_datetime',
            'subtotal',
            'status',
            'ternary_orders',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id','service_name','package_name','location_locality', 'order_id', 'subtotal', 'created_at', 'updated_at']
    
    def get_location_locality(self, obj):
        primary_order = obj.primary_order
        if primary_order.venue:
            return primary_order.venue.location.locality
        return None
    
class PrimaryOrderSerializer(serializers.ModelSerializer):
    """
    Full read serializer for PrimaryOrder with nested SecondaryOrders
    and their TernaryOrders.
    """

    secondary_orders = SecondaryOrderSerializer(many=True, read_only=True)

    venue_name = serializers.CharField(
        source='venue.name',
        read_only=True,
        allow_null=True,
    )
    service_name = serializers.CharField(
        source='service.name',
        read_only=True,
        allow_null=True,
    )
    package_name = serializers.CharField(
        source='package.name',
        read_only=True,
        allow_null=True,
    )
    patient      = PatientMiniSerializer(read_only=True)
    user_email   = serializers.CharField(source='user.email', read_only=True)

    class Meta:
        model = PrimaryOrder
        fields = [
            'id',
            'order_id',
            'booking_entity',
            'booking_type',
            'status',
            # relations
            'patient',
            'user',
            'user_email',
            'venue',
            'venue_name',
            'service',
            'service_name',
            'package',
            'package_name',
            'client_address',
            # financials
            'total_bill',
            # dates
            'start_datetime',
            'end_datetime',
            'auto_continue',
            'discount_amount',
            'premium_amount',
            # nested
            'secondary_orders',
            # timestamps
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id',
            'order_id',
            'total_bill',
            'booking_entity',
            'booking_type',
            'created_at',
            'updated_at'
        ]

    def validate(self, data):
        start_datetime = data.get('start_datetime')
        end_datetime   = data.get('end_datetime')

        if start_datetime and end_datetime:
            if start_datetime >= end_datetime:
                raise serializers.ValidationError(
                    "Start datetime must be before end datetime."
                )

        return data

class PrimaryOrderCreateSerializer(serializers.ModelSerializer):
    """
    Write serializer for creating a PrimaryOrder.

    'dates' is an optional write-only field:
      - DAILY  → list of "YYYY-MM-DD" strings
      - HOURLY → dict of {"YYYY-MM-DD": ["HH:MM:SS", ...]}

    When 'dates' is provided, 'start_datetime' / 'end_datetime' are optional
    because the model derives them from the date list.
    When 'dates' is absent, both datetime fields are required.
    """

    dates = serializers.JSONField(
        required=False,
        write_only=True,
        help_text=(
            "DAILY: list of YYYY-MM-DD strings. "
            "HOURLY: dict of {YYYY-MM-DD: [HH:MM:SS, ...]}. "
            "Omit to use full start_datetime–end_datetime range."
        ),
    )

    class Meta:
        model = PrimaryOrder
        fields = [
            'patient',
            'venue',
            'service',
            'package',
            'booking_type',
            'client_address',
            'start_datetime',
            'end_datetime',
            'discount_amount',
            'premium_amount',
            'auto_continue',
            'dates',
        ]
        extra_kwargs = {
            'service':         {'required': False},
            'venue':           {'required': False},
            'start_datetime':  {'required': False},
            'end_datetime':    {'required': False},
            'client_address':  {'required': False},
            'discount_amount':  {'required': False},
            'premium_amount':    {'required': False},
        }

    def validate(self, data):
        data["booking_entity"] = BookingEntity.SERVICE
    
        has_dates      = 'dates' in data
        start_datetime = data.get('start_datetime')
        end_datetime   = data.get('end_datetime')

        if not has_dates:
            # Full-range mode: both datetime fields are mandatory
            if not start_datetime:
                raise serializers.ValidationError(
                    {"start_datetime": "Required when 'dates' is not provided."}
                )
            if not end_datetime:
                raise serializers.ValidationError(
                    {"end_datetime": "Required when 'dates' is not provided."}
                )
            if start_datetime >= end_datetime:
                raise serializers.ValidationError(
                    {"start_datetime": "Start datetime must be before end datetime."}
                )
        else:
            # Date-list mode: validate that the provided datetimes are consistent
            # if someone accidentally sends both (we just ignore start/end in the view).
            if start_datetime and end_datetime and start_datetime >= end_datetime:
                raise serializers.ValidationError(
                    {"start_datetime": "Start datetime must be before end datetime."}
                )

        return data

    def create(self, validated_data):
        # Strip write-only 'dates' — handled separately in the ViewSet
        validated_data.pop('dates', None)
        
        return PrimaryOrder.objects.create(**validated_data)

class PaymentSerializer(serializers.ModelSerializer):
    """Serializer for Payment model"""

    class Meta:
        model = Payment
        fields = [
            'id',
            'amount',
            'method',
            'paid_date',
            'reference',
            'is_verified',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'reference','created_at','updated_at',]

class PaymentCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating payments"""

    class Meta:
        model = Payment
        fields = [
            'amount',
            'method',
            'reference',
            'paid_date',
            'is_verified',
        ]

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError(
                "Payment amount must be greater than zero."
            )
        return value

class TotalInvoiceSerializer(serializers.ModelSerializer):
    """
    Unified serializer for TotalInvoice list and detail views.
    'booking' now refers to a PrimaryOrder.
    """

    payments = PaymentSerializer(many=True, read_only=True)
    booking = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = TotalInvoice
        fields = [
            'id',
            'invoice_number',
            'period_start',
            'period_end',
            'subtotal',
            'discount_amount',
            'premium_amount',
            'tax_amount',
            'total_amount',
            'paid_amount',
            'remaining_amount',
            'status',
            'due_date',
            'issued_date',
            'created_at',
            'updated_at',
            
            'booking',
            'payments',
        ]
        read_only_fields = [
            'id',
            'invoice_number',
            'total_amount',
            'remaining_amount',
            'paid_amount',
        ]

    def validate_discount_amount(self, value):
        if value < 0:
            raise serializers.ValidationError("Discount amount cannot be negative.")
        return value

    def validate_tax_amount(self, value):
        if value < 0:
            raise serializers.ValidationError("Tax amount cannot be negative.")
        return value
    
    def get_booking(self, obj):
        """Extract booking details from secondary or ternary order"""
        order = obj.secondary_order or obj.ternary_order
        
        if not order:
            return None
        
        if hasattr(order, 'primary_order'):
            booking_obj = order.primary_order
        else:
            booking_obj = order
        
        return {
            "order_id": booking_obj.order_id,
            "venue": booking_obj.venue.name,
            "locality": booking_obj.venue.location.locality,
            "location": booking_obj.venue.location.full_address(),
            "service": booking_obj.service.name,
            "package": booking_obj.package.name,
        }

class InvoiceSummarySerializer(serializers.Serializer):
    """Serializer for invoice summary / statistics"""

    total_invoices        = serializers.IntegerField()
    total_amount          = serializers.DecimalField(max_digits=12, decimal_places=2)
    paid_amount           = serializers.DecimalField(max_digits=12, decimal_places=2)
    remaining_amount      = serializers.DecimalField(max_digits=12, decimal_places=2)
    unpaid_count          = serializers.IntegerField()
    partially_paid_count  = serializers.IntegerField()
    paid_count            = serializers.IntegerField()

    