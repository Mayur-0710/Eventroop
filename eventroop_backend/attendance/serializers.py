# serializers.py
from rest_framework import serializers
from .models import Attendance, AttendanceStatus,AttendanceReport


class AttendanceStatusSerializer(serializers.ModelSerializer):    
    class Meta:
        model = AttendanceStatus
        fields = "__all__"
        read_only_fields = ['owner']


class AttendanceSerializer(serializers.ModelSerializer):
    # Quick access fields
    status_label = serializers.CharField(source='status.label', read_only=True)
    status_code = serializers.CharField(source='status.code', read_only=True)
    
    class Meta:
        model = Attendance
        fields = [
            'user',
            'date',
            'duration',
            'status',
            'status_label',
            'status_code',
            'reason',
        ]
    
    def validate(self, data):
        """Validate attendance data"""
        user = data.get('user')
        date = data.get('date')
        
        # Check for duplicate attendance (only for create, not update)
        if not self.instance:
            if Attendance.objects.filter(user=user, date=date).exists():
                raise serializers.ValidationError(
                    f"Attendance for {user.get_full_name()} on {date} already exists."
                )
        
        return data

from decimal import Decimal
from rest_framework import serializers
from .models import AttendanceReport


class AttendanceReportSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(
        source="user.get_full_name",
        read_only=True
    )
    employee_id = serializers.CharField(
        source="user.employee_id",
        read_only=True
    )

    class Meta:
        model = AttendanceReport
        fields = [
            "id",
            "user",
            "user_name",
            "employee_id",

            "start_date",
            "end_date",
            "period_type",

            "present_days",
            "absent_days",
            "half_day_count",
            "paid_leave_days",
            "weekly_Offs",
            "unpaid_leaves",

            "total_payable_days",
            "total_payable_hours",

        ]

        read_only_fields = ["created_at", "updated_at"]

class AbsentAttendanceSerializer(serializers.Serializer):
    owner_id = serializers.IntegerField(required=True)
    date = serializers.DateField(required=True)
    absentee_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=True,
        allow_empty=False
    )

    def validate_absentee_ids(self, value):
        seen = set()
        cleaned = []
        for item in value:
            if item not in seen:
                seen.add(item)
                cleaned.append(item)
        return cleaned