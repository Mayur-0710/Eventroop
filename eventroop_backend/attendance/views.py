from django.utils import timezone
from rest_framework import status,generics
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet
from django.db import transaction
from django.shortcuts import get_object_or_404
from accounts.models import CustomUser

from .models import Attendance, AttendanceStatus,AttendanceReport
from .utils import AttendanceCalculator
from .permissions import IsSuperUserOrOwnerOrReadOnly
from .serializers import (
    AttendanceSerializer,
    AttendanceStatusSerializer,
    AttendanceReportSerializer,
    AbsentAttendanceSerializer,
)

class AttendanceStatusViewSet(ModelViewSet):
    serializer_class = AttendanceStatusSerializer
    permission_classes = [IsAuthenticated, IsSuperUserOrOwnerOrReadOnly]

    def get_queryset(self):
        user = self.request.user

        # Superuser → everything
        if user.is_superuser:
            return AttendanceStatus.objects.all()

        # Global statuses (created by superuser)
        global_qs = AttendanceStatus.objects.filter(owner__is_superuser=True)

        # Owner → global + own
        if user.is_owner:
            return global_qs | AttendanceStatus.objects.filter(owner=user)

        # Staff / Manager → global + their owner's
        if hasattr(user, "hierarchy") and user.hierarchy.owner:
            return global_qs | AttendanceStatus.objects.filter(
                owner=user.hierarchy.owner
            )

        return global_qs.none()

class AttendanceView(APIView):
    """
    GET: List all attendance records with filters
    POST: Create or Update attendance
    If attendance exists for user+date, update it. Otherwise create new.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        # Admin → see everything
        if user.is_superuser:
            queryset = Attendance.objects.all()
        # Owner → see attendance of their staff + managers
        elif user.is_owner:
            queryset = Attendance.objects.filter(user__hierarchy__owner=user)
        # Staff or Manager → see only their own attendance
        else:
            queryset = Attendance.objects.filter(user=user)

        # Apply filters from query parameters
        user_id = request.query_params.get('user_id', None)
        if user_id:
            queryset = queryset.filter(user_id=user_id)
        
        start_date = request.query_params.get('start_date', None)
        end_date = request.query_params.get('end_date', None)
        
        if start_date:
            queryset = queryset.filter(date__gte=start_date)
        if end_date:
            queryset = queryset.filter(date__lte=end_date)
        
        status_code = request.query_params.get('status', None)
        if status_code:
            queryset = queryset.filter(status__code=status_code)
        
        date = request.query_params.get('date', None)
        if date:
            queryset = queryset.filter(date=date)

        queryset = queryset.select_related('user', 'status').order_by('-date')
        serializer = AttendanceSerializer(queryset, many=True)
        
        return Response({
            'count': queryset.count(),
            'results': serializer.data
        }, status=status.HTTP_200_OK)

    def post(self, request):
        user_id = request.data.get('user')
        date = request.data.get('date')

        # Validate required fields
        if not user_id:
            return Response(
                {'error': 'user field is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if not date:
            return Response(
                {'error': 'date field is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Check if attendance already exists
        try:
            attendance = Attendance.objects.select_related('user', 'status').get(
                user_id=user_id, 
                date=date
            )
            # Update existing attendance
            serializer = AttendanceSerializer(attendance, data=request.data, partial=True)
            if serializer.is_valid():
                serializer.save()
                return Response(
                    {
                        'message': 'Attendance updated successfully',
                        'data': serializer.data
                    },
                    status=status.HTTP_200_OK
                )
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            
        except Attendance.DoesNotExist:
            # Create new attendance
            serializer = AttendanceSerializer(data=request.data)
            if serializer.is_valid():
                serializer.save()
                return Response(
                    {
                        'message': 'Attendance created successfully',
                        'data': serializer.data
                    },
                    status=status.HTTP_201_CREATED
                )
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        

class AttendanceReportView(generics.ListAPIView):
    serializer_class = AttendanceReportSerializer
    permission_classes = [IsAuthenticated]

    queryset = AttendanceReport.objects.select_related("user")

    #  Exact match filters
    filterset_fields = [
        "user_id",
        "period_type",
        "start_date",
        "end_date",
    ]

    #  Search (LIKE %term%)
    search_fields = [
        "user__first_name",
        "user__last_name",
        "user__email",
        "period_type",
    ]

    #  Ordering
    ordering_fields = [
        "start_date",
        "end_date",
        "created_at",
        "updated_at",
    ]
    ordering = ["-start_date"]
    
    def get_queryset(self):
        user = self.request.user
        
        user_id = self.request.query_params.get("user_id")
        if user_id:
            report_user = get_object_or_404(CustomUser, id=user_id)

            with transaction.atomic():
                AttendanceCalculator(report_user).get_all_periods_attendance()

        if user.is_superuser:
            return self.queryset

        if user.is_owner:
            return self.queryset.filter(user__hierarchy__owner=user)

        if user.is_manager or user.is_vsre_staff:
            return self.queryset.filter(user=user)
        
class AbsentAttendanceView(APIView):
    """
    POST: Mark multiple existing attendance rows as ABSENT for a given date.
    This API does NOT create missing attendance rows.
    Present attendance must already be created by cron.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = AbsentAttendanceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        owner_id = serializer.validated_data["owner_id"]
        att_date = serializer.validated_data["date"]
        absentee_ids = serializer.validated_data["absentee_ids"]

        user = request.user

        # Permission checks
        if not user.is_superuser and not user.is_owner:
            return Response(
                {"error": "You do not have permission to mark attendance."},
                status=status.HTTP_403_FORBIDDEN
            )

        if not user.is_superuser and user.is_owner and user.id != owner_id:
            return Response(
                {"error": "You can only mark attendance for your own staff."},
                status=status.HTTP_403_FORBIDDEN
            )

        valid_users = CustomUser.objects.filter(
            id__in=absentee_ids,
            hierarchy__owner_id=owner_id
        ).only("id")

        valid_user_ids = list(valid_users.values_list("id", flat=True))
        invalid_user_ids = [uid for uid in absentee_ids if uid not in valid_user_ids]

        if not valid_user_ids:
            return Response(
                {
                    "success": False,
                    "message": "No valid staff found for this owner.",
                    "requested_user_ids": absentee_ids,
                    "invalid_user_ids": invalid_user_ids,
                    "updated_count": 0,
                    "not_marked_user_ids": [],
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            absent_status = AttendanceStatus.objects.get(code="ABSENT", is_active=True)
        except AttendanceStatus.DoesNotExist:
            return Response(
                {"error": "AttendanceStatus with code 'ABSENT' not found."},
                status=status.HTTP_400_BAD_REQUEST
            )

        existing_user_ids = set(
            Attendance.objects.filter(
                user_id__in=valid_user_ids,
                date=att_date
            ).values_list("user_id", flat=True)
        )

        not_marked_user_ids = [uid for uid in valid_user_ids if uid not in existing_user_ids]

        with transaction.atomic():
            updated_count = Attendance.objects.filter(
                user_id__in=existing_user_ids,
                date=att_date
            ).update(
                status=absent_status,
                updated_at=timezone.now()
            )

        from .signals import update_attendance_report
        for affected_user in CustomUser.objects.filter(id__in=list(existing_user_ids)):
            update_attendance_report(affected_user, att_date)

        return Response(
            {
                "success": True,
                "message": "Absent attendance processed successfully.",
                "owner_id": owner_id,
                "date": att_date,
                "requested_user_ids": absentee_ids,
                "valid_user_ids": valid_user_ids,
                "invalid_user_ids": invalid_user_ids,
                "not_marked_user_ids": not_marked_user_ids,
                "updated_count": updated_count,
            },
            status=status.HTTP_200_OK
        )
