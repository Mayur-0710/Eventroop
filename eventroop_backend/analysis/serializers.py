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