from django.db import models,transaction
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.core.validators import MinValueValidator, MaxValueValidator, RegexValidator
from django.core.exceptions import ValidationError
from django.utils import timezone
from accounts.models import CustomUser
from django.contrib.contenttypes import fields, models as ct_models
from decimal import Decimal
import uuid
from .constants import *
# TODO : remove functional auto_update_status from save method
from .utils import auto_update_status,generate_order_id,calculate_amount
from datetime import timedelta,datetime, time
from dateutil.relativedelta import relativedelta
        
class Location(models.Model):
        
    user = models.ForeignKey(
        CustomUser,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="user_location",
    )
    location_type = models.CharField(max_length=20, choices=BookingType.choices)
    building_name = models.CharField(max_length=250)
    address_line1 = models.CharField(max_length=250)
    address_line2 = models.CharField(max_length=250, blank=True, null=True)
    locality = models.CharField(max_length=250)
    city = models.CharField(max_length=250)
    state = models.CharField(max_length=250)
    postal_code = models.CharField(max_length=20)

    
    class Meta:
        verbose_name = "Location"
        verbose_name_plural = "Locations"
        indexes = [
            models.Index(fields=['location_type']),
            models.Index(fields=['city', 'state']),
        ]

    def __str__(self):
        return f"{self.building_name} ({self.city})"

    def full_address(self):
        """Return formatted full address"""
        parts = [
            self.building_name,
            self.address_line1,
            self.address_line2,
            self.locality,
            self.city,
            self.state,
            self.postal_code,
        ]
        return ", ".join(filter(None, parts))

class Package(models.Model):   
    owner = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name='packages',
        limit_choices_to={"user_type": "VSRE_OWNER"}
    )

    # Polymorphic relation: belongs_to → Venue | Service | Resource
    content_type = models.ForeignKey(
        ct_models.ContentType,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        limit_choices_to={
            'model__in': [
                'venue','service','resource'
            ]
        }
    )
    object_id = models.PositiveIntegerField(null=True, blank=True)
    belongs_to = fields.GenericForeignKey('content_type', 'object_id')

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    package_type = models.CharField(
        max_length=20,
        choices=BookingType.choices,
        default=BookingType.IN_HOUSE
    )
    period = models.CharField(
        max_length=10,
        choices=PeriodChoices.choices,
        default=PeriodChoices.MONTHLY
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    
    class Meta:
        verbose_name = "Package"
        verbose_name_plural = "Packages"
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['content_type', 'object_id']),
            models.Index(fields=['package_type', 'is_active']),
            models.Index(fields=['owner', '-created_at']),
        ]

    def __str__(self):
        return f"{self.name} ({self.package_type})"

class Patient(models.Model):
    """Model for storing patient registration and medical information"""
    
    GENDER_CHOICES = [
        ('male', 'Male'),
        ('female', 'Female'),
        ('other', 'Other'),
        ('prefer-not-to-say', 'Prefer not to say'),
    ]
    
    BLOOD_GROUP_CHOICES = [
        ('A+', 'A+'),
        ('A-', 'A-'),
        ('B+', 'B+'),
        ('B-', 'B-'),
        ('AB+', 'AB+'),
        ('AB-', 'AB-'),
        ('O+', 'O+'),
        ('O-', 'O-'),
    ]
    
    ID_PROOF_CHOICES = [
        ('aadhar', 'Aadhar Card'),
        ('pan', 'PAN Card'),
        ('passport', 'Passport'),
        ('driving-license', 'Driving License'),
        ('voter-id', 'Voter ID'),
        ('other', 'Other'),
    ]
    
    PAYMENT_MODE_CHOICES = [
        ('cash', 'Cash'),
        ('credit-card', 'Credit Card'),
        ('debit-card', 'Debit Card'),
        ('net-banking', 'Net Banking'),
        ('upi', 'UPI'),
        ('cheque', 'Cheque'),
        ('bank-transfer', 'Bank Transfer'),
    ]
    
    # Validators
    phone_regex = RegexValidator(
        regex=r'^\d{10}$',
        message="Phone number must be 10 digits"
    )
    
    registered_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        related_name='registered_patients',
        help_text="User who registered this patient"
    )

    # Basic Information
    patient_id = models.CharField(max_length=20, blank=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField(null=True, blank=True)
    
    # Contact Information
    phone = models.CharField(max_length=10, validators=[phone_regex])
    address = models.TextField()
    age = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(100)]
    )
    
    # Emergency Contacts
    emergency_contact = models.CharField(max_length=100)
    emergency_phone = models.CharField(max_length=10, validators=[phone_regex])
    emergency_contact_2 = models.CharField(max_length=100, null=True, blank=True)
    emergency_phone_2 = models.CharField(
        max_length=10,
        null=True,
        blank=True,
        validators=[phone_regex]
    )
    
    # Medical Information
    medical_conditions = models.TextField(null=True, blank=True)
    allergies = models.TextField(null=True, blank=True)
    present_health_condition = models.TextField(null=True, blank=True)
    
    # Personal Details
    gender = models.CharField(max_length=20, choices=GENDER_CHOICES)
    blood_group = models.CharField(max_length=3, choices=BLOOD_GROUP_CHOICES, null=True, blank=True)
    preferred_language = models.CharField(max_length=20, null=True, blank=True)
    
    # Identification
    id_proof = models.CharField(max_length=20, choices=ID_PROOF_CHOICES)
    id_proof_number = models.CharField(max_length=50)
    patient_documents = models.FileField(upload_to='patient_documents/')
    
    # Professional Background
    education_qualifications = models.TextField(null=True, blank=True)
    earlier_occupation = models.CharField(max_length=200, null=True, blank=True)
    year_of_retirement = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1950), MaxValueValidator(timezone.localtime().year)]
    )
    
    # Payment Information
    registration_fee = models.DecimalField(max_digits=10, decimal_places=2, default=5000.00)
    advance_payment = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100000)]
    )
    payment_mode = models.CharField(
        max_length=20,
        choices=PAYMENT_MODE_CHOICES,
        null=True,
        blank=True
    )
    registration_date = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    
    class Meta:
        verbose_name = "Patient"
        verbose_name_plural = 'Patients'
        ordering = ['-registration_date']
        indexes = [
            models.Index(fields=['email']),
            models.Index(fields=['phone']),
            models.Index(fields=['-registration_date']),
            models.Index(fields=['registered_by']),
            models.Index(fields=['first_name', 'last_name']),
        ]
    
    def __str__(self):
        email_display = self.email if self.email else "No Email"
        return f"{self.get_full_name()} - {email_display}"
    
    def get_full_name(self):
        """Return patient's full name"""
        return f"{self.first_name} {self.last_name}"
      
    def get_total_payment(self):
        """Return total payment (registration fee + advance payment)"""
        total = self.registration_fee
        if self.advance_payment:
            total += self.advance_payment
        return total

    def save(self, *args, **kwargs):
        is_new = self.pk is None

        super().save(*args, **kwargs)

        # After first save, ID exists
        if is_new and not self.patient_id:
            self.patient_id = f"{self.pk:05}"
            super().save(update_fields=["patient_id"])

class PrimaryOrder(models.Model):
    order_id = models.CharField(max_length=50, blank=True)

    booking_entity = models.CharField(
        max_length=20,
        choices=BookingEntity.choices,
        default=BookingEntity.VENUE,
        db_index=True,
    )
    user = models.ForeignKey(
        "accounts.CustomUser", on_delete=models.CASCADE, db_index=True
    )
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, db_index=True)
    venue = models.ForeignKey(
        "venue_manager.Venue",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        db_index=True,
    )
    service = models.ForeignKey(
        "venue_manager.Service",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        db_index=True,
    )
    package = models.ForeignKey(
        Package, on_delete=models.CASCADE, related_name="primary_orders"
    )

    start_datetime = models.DateTimeField(db_index=True)
    end_datetime = models.DateTimeField(db_index=True)

    total_bill = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00"), editable=False
    )
    discount_amount  = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    premium_amount   = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    booking_type = models.CharField(
        max_length=25,
        choices=BookingType.choices,
        default=BookingType.OPD,
        db_index=True,
    )
    status = models.CharField(
        max_length=25,
        choices=BookingStatus.choices,
        default=BookingStatus.DRAFT,
        db_index=True,
    )
    status_locked = models.BooleanField(default=False)
    auto_continue = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.order_id}"

    # ── Lifecycle ──────────────────────────────────────────────────────────────
    def save(self, *args, **kwargs):
        skip_auto_status = kwargs.pop("skip_auto_status", False)
        update_fields = kwargs.get("update_fields")
        is_targeted_save = update_fields is not None

        if not is_targeted_save:
            if not skip_auto_status:
                self.status = auto_update_status(self.start_datetime, self.end_datetime)

            self.booking_type = self.package.package_type

        super().save(*args, **kwargs)

        if not self.order_id:
            self.order_id = generate_order_id(self)
            # Use super() to avoid re-triggering this save() hook
            super().save(update_fields=["order_id"])
        self.recalculate_total()
        
    # ── Sub-order generation ───────────────────────────────────────────────────
    def generate_secondary_from_random_dates(self, dates):
        """
        Create SecondaryOrders from:
        - list[date] → DAILY
        - dict[date] = [time, time] → HOURLY
        """
        if not dates:
            return

        period_type = self.package.period

        with transaction.atomic():
            objects = []

            # DAILY
            if period_type == PeriodChoices.DAILY and isinstance(dates, list):
                for d in dates:
                    start_dt = timezone.make_aware(datetime.combine(d, time.min))
                    end_dt = timezone.make_aware(datetime.combine(d, time.max))

                    objects.append(
                        SecondaryOrder(
                            primary_order=self,
                            start_datetime=start_dt,
                            end_datetime=end_dt,
                            subtotal= (
                                (
                                    calculate_amount(start_dt, end_dt, self.package)
                                    + self.premium_amount
                                ) - self.discount_amount
                                ),
                        )
                    )

            # HOURLY
            elif period_type == PeriodChoices.HOURLY and isinstance(dates, dict):
                for date, slots in dates.items():
                    if not slots:
                        continue

                    start_dt = timezone.make_aware(datetime.combine(date, min(slots)))
                    end_dt = timezone.make_aware(datetime.combine(date, max(slots)))

                    objects.append(
                        SecondaryOrder(
                            primary_order=self,
                            start_datetime=start_dt,
                            end_datetime=end_dt,
                            subtotal=(
                                (
                                    calculate_amount(start_dt, end_dt, self.package)
                                    + self.premium_amount
                                ) - self.discount_amount
                                ),
                        )
                    )

            # Nothing to create
            if not objects:
                return

            # Bulk create/update
            SecondaryOrder.objects.bulk_create(
                objects,
                update_conflicts=True,
                unique_fields=["primary_order", "start_datetime", "end_datetime"],
                update_fields=["subtotal"],
            )

            # Only fetch affected records (not all)
            updated_secondaries = list(
                SecondaryOrder.objects.filter(
                    primary_order=self,
                    start_datetime__in=[obj.start_datetime for obj in objects],
                )
            )

            for obj in updated_secondaries:
                if not obj.order_id:
                    obj.status = auto_update_status(obj.start_datetime, obj.end_datetime)
                    obj.order_id = generate_order_id(obj)

            SecondaryOrder.objects.bulk_update(
                updated_secondaries,
                ["status", "order_id"],
            )

            # Recalculate total once
            self.recalculate_total()

    def generate_secondary_full_range_dates(self):
        """Create SecondaryOrders by splitting the full booking range into periods."""
        period_type = self.package.period
        pkg_price = self.package.price
        discount_amount = self.discount_amount
        premium_amount = self.premium_amount
        final_price = ((pkg_price + premium_amount)- discount_amount)

        period_generators = {
            PeriodChoices.MONTHLY: self._get_monthly_periods,
            PeriodChoices.WEEKLY: self._get_weekly_periods,
            PeriodChoices.DAILY: self._get_daily_periods,
            PeriodChoices.HOURLY: self._get_daily_periods,
        }

        generator = period_generators.get(period_type)
        if not generator:
            return

        periods = generator()
        if not periods:
            return

        with transaction.atomic():
            objects = [
                SecondaryOrder(
                    primary_order=self,
                    start_datetime=slot_start,
                    end_datetime=slot_end,
                    subtotal=final_price ,
                )
                for slot_start, slot_end in periods
            ]

            created = SecondaryOrder.objects.bulk_create(
                objects,
                update_conflicts=True,
                unique_fields=["primary_order", "start_datetime", "end_datetime"],
                update_fields=["subtotal"],
            )

            for obj in created:
                obj.status = auto_update_status(obj.start_datetime, obj.end_datetime)
                obj.order_id = generate_order_id(obj)

            SecondaryOrder.objects.bulk_update(created, ["status", "order_id"])

            self.recalculate_total()

    # ── Period helpers ─────────────────────────────────────────────────────────
    def _get_monthly_periods(self):
        """
        Split range into calendar-month periods anchored to the start date.
        Example: Feb 4 → Mar 3  =  one period (Feb 4, Mar 3)
                Feb 4 → Apr 9  =  (Feb 4, Mar 3), (Mar 4, Apr 3), (Apr 4, Apr 9)
        """
        periods = []
        current = self.start_datetime

        while current < self.end_datetime:
            next_period_start = current + relativedelta(months=1)
            # Period ends the day BEFORE next cycle starts
            period_end = min(
                next_period_start - timedelta(days=1),
                self.end_datetime
            )
            periods.append((current, period_end))
            current = next_period_start

        return periods

    def _get_weekly_periods(self):
        return self._split_into_days(chunk_days=7)

    def _get_daily_periods(self):
        return self._split_into_days()

    def _split_into_days(self, chunk_days=None):
        """Shared logic: split range into day-level periods, optionally in N-day chunks."""
        if self.start_datetime >= self.end_datetime:
            return []

        periods = []
        current = self.start_datetime

        while current < self.end_datetime:
            chunk_end = (
                min(current + timedelta(days=chunk_days), self.end_datetime)
                if chunk_days
                else self.end_datetime
            )

            day_cursor = current
            while day_cursor < chunk_end:
                day_end = min(
                    day_cursor.replace(hour=23, minute=59, second=59, microsecond=999999),
                    self.end_datetime,
                )
                periods.append((day_cursor, day_end))
                day_cursor += timedelta(days=1)

            current = chunk_end

        return periods

    # ── Actions ────────────────────────────────────────────────────────────────
    def reschedule(self, new_start, new_end, new_package_id=None, discount_amount=None, premium_amount=None):
        now = timezone.now()

        if new_start >= new_end:
            raise ValidationError("Start date must be before end date.")
        if self.end_datetime < now:
            raise ValidationError("Past bookings cannot be rescheduled.")

        is_ongoing = self.start_datetime <= now <= self.end_datetime
        if is_ongoing and new_start != self.start_datetime:
            raise ValidationError("Cannot change start date of an in-progress booking.")

        with transaction.atomic():
            self.start_datetime = new_start
            self.end_datetime = new_end

            if new_package_id:
                self.package_id = new_package_id
            if discount_amount is not None:
                self.discount_amount = discount_amount
            if premium_amount is not None:
                self.premium_amount = premium_amount

            self.save(skip_auto_status=True)
            self._generate_secondary_and_ternary_orders()

    def recalculate_total(self):
        total = self.secondary_orders.aggregate(
            total=Coalesce(Sum("subtotal"), Decimal("0.00"))
        )["total"]

        self.total_bill = total
        super().save(update_fields=["total_bill"])

class SecondaryOrder(models.Model):
    """One record per period (month/week/day) within a PrimaryOrder span."""

    primary_order = models.ForeignKey(
        PrimaryOrder,
        on_delete=models.CASCADE,
        related_name="secondary_orders",
        null=True, blank=True,
        db_index=True,
    )
    order_id = models.CharField(max_length=50, blank=True)

    start_datetime = models.DateTimeField(db_index=True)
    end_datetime = models.DateTimeField(db_index=True)

    subtotal = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00"), editable=False
    )
    status = models.CharField(
        max_length=25,
        choices=BookingStatus.choices,
        default=BookingStatus.DRAFT,
        db_index=True,
    )
    status_locked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("primary_order", "start_datetime", "end_datetime")
        ordering = ["start_datetime", "end_datetime"]

    def __str__(self):
        return (
            f"{self.primary_order.order_id}"
            f" → {self.order_id}"
        )

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def save(self, *args, **kwargs):
        skip_auto_status = kwargs.pop("skip_auto_status", False)
        skip_primary_recalc = kwargs.pop("skip_primary_recalc", False)

        update_fields = kwargs.get("update_fields")
        is_targeted_save = update_fields is not None

        if not is_targeted_save and not skip_auto_status:
            self.status = auto_update_status(self.start_datetime, self.end_datetime)

        super().save(*args, **kwargs)

        if not self.order_id:
            self.order_id = generate_order_id(self)
            super().save(update_fields=["order_id"])

    # ── Calculations ───────────────────────────────────────────────────────────
    def recalculate_subtotal(self):
        """
        Recompute subtotal as: base_price + Sum(TernaryOrder.subtotal)
        Then cascade up to PrimaryOrder and sideways to TotalInvoice.
        """
        base_price = self.primary_order.package.price
        ternary_total = self.ternary_orders.aggregate(
            total=Coalesce(Sum("subtotal"), Decimal("0.00"))
        )["total"]

        self.subtotal = base_price + ternary_total
        super().save(update_fields=["subtotal"])

        if self.primary_order_id:
            self.primary_order.recalculate_total()

class TernaryOrder(models.Model):
    """One record per service/booking line item within a SecondaryOrder."""
    
    secondary_order = models.ForeignKey(
        SecondaryOrder,
        on_delete=models.CASCADE,
        related_name="ternary_orders",
        db_index=True,
    )
    order_id = models.CharField(max_length=50, blank=True)
    venue = models.ForeignKey(
        "venue_manager.Venue",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        db_index=True,
    )
    service = models.ForeignKey(
        "venue_manager.Service",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        db_index=True,
    )
    package = models.ForeignKey(
        "Package", on_delete=models.CASCADE, related_name="ternary_orders"
    )

    booking_entity = models.CharField(
        max_length=20,
        choices=BookingEntity.choices,
        default=BookingEntity.SERVICE,
        db_index=True,
    )
    booking_type = models.CharField(
        max_length=25,
        choices=BookingType.choices,
        default=BookingType.OPD,
        db_index=True,
    )

    start_datetime = models.DateTimeField(db_index=True)
    end_datetime = models.DateTimeField(db_index=True)

    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    premium_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))

    subtotal = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00"), editable=False
    )
    status = models.CharField(
        max_length=25,
        choices=BookingStatus.choices,
        default=BookingStatus.DRAFT,
        db_index=True,
    )
    status_locked = models.BooleanField(default=False)
    
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["start_datetime"]

    def __str__(self):
        return (
            f"{self.secondary_order.primary_order.order_id}"
            f" → {self.secondary_order}"
            f" → {self.order_id}"
        )

    # ── Lifecycle ──────────────────────────────────────────────────────────────
    def save(self, *args, **kwargs):
        skip_auto_status = kwargs.pop("skip_auto_status", False)
        update_fields = kwargs.get("update_fields")
        is_targeted_save = update_fields is not None
        
        if not is_targeted_save:
            if not skip_auto_status:
                self.status = auto_update_status(self.start_datetime, self.end_datetime)
            self.booking_type = self.package.package_type
        
        base_amount = calculate_amount(self.start_datetime,self.end_datetime,self.package)

        discount = self.discount_amount or Decimal("0.00")
        premium = self.premium_amount or Decimal("0.00")

        self.subtotal = base_amount + premium - discount 
        super().save(*args, **kwargs)

        if not self.order_id:
            self.order_id = generate_order_id(self)
            super().save(update_fields=["order_id"])

class TotalInvoice(models.Model):
    """One invoice per SecondaryOrder or TernaryOrder."""

    secondary_order = models.ForeignKey(
        SecondaryOrder,
        null=True, blank=True,
        related_name="invoices",
        on_delete=models.CASCADE,
    )
    ternary_order = models.ForeignKey(
        TernaryOrder,
        null=True, blank=True,
        related_name="invoices",
        on_delete=models.CASCADE,
    )
    # Ternary invoices point to their parent Secondary invoice for easy aggregation
    parent_invoice = models.ForeignKey(
        "self",
        null=True, blank=True,
        related_name="ternary_invoices",
        on_delete=models.SET_NULL,
    )

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE)
    user    = models.ForeignKey("accounts.CustomUser", on_delete=models.CASCADE)

    invoice_number = models.CharField(max_length=50, unique=True, blank=True)

    period_start = models.DateTimeField(db_index=True)
    period_end   = models.DateTimeField(db_index=True)

    subtotal         = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    discount_amount  = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    premium_amount   = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    tax_amount       = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total_amount     = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    paid_amount      = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    remaining_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    status   = models.CharField(max_length=20, choices=InvoiceStatus.choices, default=InvoiceStatus.UNPAID, db_index=True)
    due_date = models.DateField(null=True, blank=True)

    issued_date = models.DateField(default=timezone.now)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-issued_date"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(secondary_order__isnull=False, ternary_order__isnull=True) |
                    models.Q(secondary_order__isnull=True,  ternary_order__isnull=False)
                ),
                name="invoice_must_link_to_exactly_one_order",
                violation_error_message="Invoice must link to exactly one order (secondary or ternary, not both or neither).",
            ),
        ]
        indexes = [
            models.Index(fields=["secondary_order", "period_start"]),
            models.Index(fields=["ternary_order",   "period_start"]),
            models.Index(fields=["user", "status"]),
        ]

    def __str__(self):
        return f"{self.invoice_number} | {self.period_start.date()} → {self.period_end.date()}"

    @property
    def invoice_type(self):
        return "SECONDARY" if self.secondary_order_id else "TERNARY"

    # ── Lifecycle ──────────────────────────────────────────────────────────────
    def save(self, *args, **kwargs):
        self.total_amount = (
            self.subtotal +  
            self.tax_amount +
            self.premium_amount - 
            self.discount_amount
        )
        
        self.remaining_amount = max(
            self.total_amount - (self.paid_amount or Decimal("0.00")),
            Decimal("0.00"),
        )
        is_new = self.pk is None
        super().save(*args, **kwargs)
        if is_new and not self.invoice_number:
            prefix = "SINV" if self.secondary_order_id else "TINV"
            self.invoice_number = f"{prefix}{self.id:08}"
            super().save(update_fields=["invoice_number"])

    # ── Factories ──────────────────────────────────────────────────────────────
    @classmethod
    def create_or_update_for_secondary(cls, secondary: "SecondaryOrder") -> "TotalInvoice":
        """
        Create or update a Secondary invoice.
        subtotal = base package price only (excludes ternary add-ons).
        """
        primary  = secondary.primary_order
        
        with transaction.atomic():
            invoice, created = cls.objects.get_or_create(
                secondary_order=secondary,
                period_start=secondary.start_datetime,
                period_end=secondary.end_datetime,
                defaults={
                    "patient":          primary.patient,
                    "user":             primary.user,
                    "subtotal":         secondary.subtotal,
                    "status":           InvoiceStatus.UNPAID
                },
            )

            if not created:
                invoice.subtotal         = secondary.subtotal
                invoice.total_amount     = invoice.subtotal + invoice.premium_amount + invoice.tax_amount - invoice.discount_amount
                invoice.remaining_amount = max(
                    invoice.total_amount - (invoice.paid_amount or Decimal("0.00")),
                    Decimal("0.00"),
                )
                super(TotalInvoice, invoice).save(
                    update_fields=["subtotal", "total_amount", "remaining_amount"]
                )

        return invoice

    @classmethod
    def create_or_update_for_ternary(cls, ternary: "TernaryOrder") -> "TotalInvoice":
        """
        Create or update a Ternary invoice.
        subtotal = ternary.subtotal (base_amount - discount + premium).
        Links to parent Secondary invoice via parent_invoice FK.
        """
        secondary      = ternary.secondary_order
        primary        = secondary.primary_order
        parent_invoice = secondary.invoices.filter(
            secondary_order=secondary
        ).first()

        with transaction.atomic():
            invoice, created = cls.objects.get_or_create(
                ternary_order=ternary,
                period_start=ternary.start_datetime,
                period_end=ternary.end_datetime,
                defaults={
                    "parent_invoice":   parent_invoice,
                    "patient":          primary.patient,
                    "user":             primary.user,
                    "subtotal":         ternary.subtotal,
                    "status":           InvoiceStatus.UNPAID,
                },
            )

            if not created:
                invoice.subtotal         = ternary.subtotal
                invoice.total_amount     = invoice.subtotal + invoice.premium_amount + invoice.tax_amount - invoice.discount_amount
                invoice.remaining_amount = max(
                    invoice.total_amount - (invoice.paid_amount or Decimal("0.00")),
                    Decimal("0.00"),
                )
                # Backfill parent link if it was missing
                if parent_invoice and not invoice.parent_invoice_id:
                    invoice.parent_invoice = parent_invoice

                super(TotalInvoice, invoice).save(update_fields=[
                    "subtotal", "total_amount", "remaining_amount", "parent_invoice",
                ])

        return invoice

    # ── Payments ───────────────────────────────────────────────────────────────
    def recalculate_payments(self):
        """Recompute paid_amount, remaining_amount and status from all payments."""

        self.paid_amount = self.payments.aggregate(
            total=Coalesce(Sum("amount"), Decimal("0.00"))
        )["total"]

        self.remaining_amount = max(
            self.total_amount - self.paid_amount, Decimal("0.00")
        )

        if self.paid_amount <= 0:
            self.status = InvoiceStatus.UNPAID
        elif self.paid_amount >= self.total_amount:
            self.status = InvoiceStatus.PAID
        else:
            self.status = InvoiceStatus.PARTIALLY_PAID

        super().save(update_fields=["paid_amount", "remaining_amount", "status"])

class Payment(models.Model):
    invoice = models.ForeignKey(
        TotalInvoice, related_name="payments", on_delete=models.CASCADE
    )
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE)

    amount    = models.DecimalField(max_digits=12, decimal_places=2)
    method    = models.CharField(max_length=20, choices=PaymentMethod.choices)
    paid_date = models.DateTimeField(default=timezone.now)
    reference = models.CharField(max_length=100, blank=True)

    is_verified = models.BooleanField(default=False)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-paid_date"]
        indexes = [
            models.Index(fields=["invoice", "created_at"]),
            models.Index(fields=["is_verified"]),
        ]

    def __str__(self):
        return f"Payment #{self.id} — {self.amount} → {self.invoice.invoice_number}"

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = f"PAY-{uuid.uuid4().hex[:10].upper()}"
        super().save(*args, **kwargs)

    def _set_verified(self, state: bool) -> bool:
        if self.is_verified == state:
            return False
        with transaction.atomic():
            self.is_verified = state
            super(Payment, self).save(update_fields=["is_verified"])
        return True

    def verify(self) -> bool:
        return self._set_verified(True)

    def unverify(self) -> bool:
        return self._set_verified(False)
