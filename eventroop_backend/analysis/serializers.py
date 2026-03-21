from rest_framework import serializers
from attendance.models import Attendance, AttendanceStatus


class SalaryPeriodSerializer(serializers.Serializer):
    """One pay period row for an employee."""

    start_date      = serializers.DateField()
    end_date        = serializers.DateField()
    calendar_days   = serializers.IntegerField()
    days_present    = serializers.DecimalField(max_digits=10, decimal_places=2)
    days_absent     = serializers.DecimalField(max_digits=10, decimal_places=2)
    base_salary     = serializers.DecimalField(max_digits=12, decimal_places=2)
    salary          = serializers.DecimalField(max_digits=12, decimal_places=2)
    total_salary    = serializers.DecimalField(max_digits=12, decimal_places=2)
    status          = serializers.CharField(allow_null=True)
    payment_date    = serializers.DateTimeField(allow_null=True)
    amount_paid     = serializers.DecimalField(max_digits=12, decimal_places=2)
    excess_balance  = serializers.DecimalField(max_digits=12, decimal_places=2)


class EmployeeSalaryAnalysisSerializer(serializers.Serializer):
    """One employee with all their salary periods nested."""

    emp_id          = serializers.CharField(allow_null=True)
    first_name      = serializers.CharField()
    middle_name     = serializers.CharField(allow_null=True)
    last_name       = serializers.CharField()
    mobile_number   = serializers.CharField()
    periods         = SalaryPeriodSerializer(many=True)

class AttendanceStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendanceStatus
        fields = ['id', 'code', 'label']

class AttendanceDailySerializer(serializers.ModelSerializer):
    status_code  = serializers.CharField(source='status.code')
    status_label = serializers.CharField(source='status.label')
    employee_id  = serializers.CharField(source='user.employee_id')
    name         = serializers.SerializerMethodField()

    class Meta:
        model = Attendance
        fields = ['date', 'status_code', 'status_label', 'employee_id', 'name']

    def get_name(self, obj):
        return obj.user.get_full_name()