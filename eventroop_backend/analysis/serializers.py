from rest_framework import serializers
from attendance.models import Attendance, AttendanceStatus


class SalaryPeriodSerializer(serializers.Serializer):
    """One pay period row for an employee."""

    start_date      = serializers.DateField()
    end_date        = serializers.DateField()
    calendar_days   = serializers.IntegerField()
    total_payable_days     = serializers.DecimalField(max_digits=10, decimal_places=2)
    total_unpaid_days     = serializers.DecimalField(max_digits=10, decimal_places=2)
    attendance_pct  = serializers.DecimalField(max_digits=10, decimal_places=2)
    salary          = serializers.DecimalField(max_digits=12, decimal_places=2)
    total_salary    = serializers.DecimalField(max_digits=12, decimal_places=2)
    payment_status          = serializers.CharField(allow_null=True)
    amount_paid     = serializers.DecimalField(max_digits=12, decimal_places=2)
    excess_balance  = serializers.DecimalField(max_digits=12, decimal_places=2)


class EmployeeSalaryAnalysisSerializer(serializers.Serializer):
    """One employee with all their salary periods nested."""

    id              = serializers.IntegerField()
    emp_id          = serializers.CharField(allow_null=True)
    first_name      = serializers.CharField()
    middle_name     = serializers.CharField(allow_null=True)
    last_name       = serializers.CharField()
    mobile_number   = serializers.CharField()
    status          = serializers.BooleanField()
    periods         = SalaryPeriodSerializer(many=True)


class AttendanceDaySerializer(serializers.Serializer):
    """
    Represents a single month's attendance record.
    Example: { "01": "P", "02": "A", "03": "H" }
    """
    def to_representation(self, instance):
        return instance  # already a dict of { "DD": "status_code" }

class UserAttendanceSerializer(serializers.Serializer):
    """
    Represents attendance data for a single user.
    """
    user = serializers.IntegerField()
    attendance = serializers.SerializerMethodField()

    def get_attendance(self, obj):
        """
        obj["attendance"] is already a structured dict:
            { "Jan-2025": { "01": "P", "02": "A" }, ... }
        We simply return it as-is.
        """
        return obj.get("attendance", {})


class MonthlyAnalyticsRowSerializer(serializers.Serializer):
    month = serializers.CharField()
    total_bookings = serializers.IntegerField()
    invoice_value = serializers.CharField()
    amt_collected = serializers.CharField()
    balance = serializers.CharField()
    collection_pct = serializers.FloatField(allow_null=True)
    pending_invoices = serializers.IntegerField()
    invoices_not_generated = serializers.IntegerField()

class MonthlyAnalyticsSummarySerializer(serializers.Serializer):
    total_bookings = serializers.IntegerField()
    total_invoice_value = serializers.CharField()
    total_amt_collected = serializers.CharField()
    total_balance = serializers.CharField()
    collection_pct = serializers.FloatField(allow_null=True)
    total_pending_invoices = serializers.IntegerField()
    total_invoices_not_generated = serializers.IntegerField()

class MonthlyAnalyticsResponseSerializer(serializers.Serializer):
    year = serializers.IntegerField()
    month = serializers.IntegerField()
    rows = MonthlyAnalyticsRowSerializer(many=True)
    summary = MonthlyAnalyticsSummarySerializer()