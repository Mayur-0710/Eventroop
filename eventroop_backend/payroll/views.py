from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .models import *
from .serializers import *
from .utils import SalaryCalculator 
from rest_framework import viewsets, status
from datetime import datetime, timedelta
from rest_framework.views import APIView
from dateutil.relativedelta import relativedelta
from django.db import transaction as db_transaction
from django.utils import timezone
from django.shortcuts import get_object_or_404
from django.db.models import Sum

class SalaryStructureViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing salary structures
    """

    serializer_class = SalaryStructureSerializer
    permission_classes = [IsAuthenticated]

    filterset_fields = [
        "user_id",
        "salary_type",
        "change_type",
        "effective_from",
    ]

    search_fields = [
        "user__email",
        "user__first_name",
        "user__last_name",
        "user__mobile_number",
    ]

    def get_queryset(self):
        """
        Get queryset based on user hierarchy
        """
        user = self.request.user

        # Admin → see everything
        if user.is_superuser:
            queryset = SalaryStructure.objects.all()

        # Owner → see salary structures of their staff + managers
        elif getattr(user, "is_owner", False):
            queryset = SalaryStructure.objects.filter(user__hierarchy__owner=user)

        # Staff or Manager → see only their own salary structure
        else:
            queryset = SalaryStructure.objects.filter(user=user)
        return queryset.select_related("user").order_by("-effective_from")

class SalaryReportAPIView(APIView):
    """
    READ-ONLY Salary Report API

    GET /api/salary-reports/
    GET /api/salary-reports/<id>/

    Filters:
    - Default: last 6 months
    - ?year=YYYY
    - ?start_date=YYYY-MM-DD
    - ?end_date=YYYY-MM-DD
    """

    permission_classes = [IsAuthenticated]

    # -------------------- Base queryset --------------------
    def get_base_queryset(self, request):
        user = request.user

        if user.is_superuser:
            return SalaryReport.objects.all()

        if user.is_owner:
            return SalaryReport.objects.filter(user__hierarchy__owner=user)

        if user.is_manager or user.is_vsre_staff:
            return SalaryReport.objects.filter(user=user)

        return SalaryReport.objects.none()

    # -------------------- Date filters --------------------
    def apply_date_filters(self, request, queryset):
        params = request.query_params

        # ----- User filter -----
        user_id = params.get("user_id")
       
        if user_id:
            try:
                user_id = int(user_id)
                queryset = queryset.filter(user_id=user_id)
            except ValueError:
                return queryset.none()
            report_user = get_object_or_404(CustomUser, id=user_id)
                
            with db_transaction.atomic():
                SalaryCalculator(report_user).refresh_salary_reports()
                
        # Initialize default date range (6 months to end of current month)
        today = datetime.now().date()
        end_date = (today.replace(day=1) + relativedelta(months=1)) - timedelta(days=1)  # Last day of current month
        start_date = (end_date - relativedelta(months=6)).replace(day=1)  # First day, 6 months back

        # ----- Year filter (highest priority) -----
        year = params.get("year")
        if year:
            try:
                year = int(year)
                start_date = datetime(year, 1, 1).date()
                end_date = datetime(year, 12, 31).date()
                return queryset.filter(start_date__gte=start_date, start_date__lte=end_date)
            except ValueError:
                return queryset.none()

        # ----- Custom date override -----
        custom_start = params.get("start_date")
        custom_end = params.get("end_date")

        if custom_start or custom_end:
            try:
                if custom_start:
                    start_date = datetime.strptime(custom_start, "%Y-%m-%d").date()
                if custom_end:
                    end_date = datetime.strptime(custom_end, "%Y-%m-%d").date()
            except ValueError:
                return queryset.none()

        # ----- Apply date filters -----
        return queryset.filter(
            start_date__gte=start_date,
            start_date__lte=end_date,
        )

    # -------------------- GET --------------------
    def get(self, request, pk=None):
        queryset = self.get_base_queryset(request)
        queryset = self.apply_date_filters(request, queryset)

        # ----- Retrieve single report -----
        if pk:
            try:
                report = queryset.get(pk=pk)
            except SalaryReport.DoesNotExist:
                return Response(
                    {"detail": "Salary report not found"},
                    status=status.HTTP_404_NOT_FOUND,
                )

            serializer = SalaryReportSerializer(report)
            return Response(serializer.data, status=status.HTTP_200_OK)

        # ----- List reports -----
        serializer = SalaryReportSerializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

class SalaryTransactionViewSet(viewsets.ModelViewSet):
    serializer_class = SalaryTransactionSerializer
    filterset_fields = ["salary_report", "status"]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            queryset = SalaryTransaction.objects.all()
        elif user.is_owner:
            queryset = SalaryTransaction.objects.filter(salary_report__user__hierarchy__owner=user)
        else:
            queryset = SalaryTransaction.objects.filter(salary_report__user=user)

        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        user_filter = self.request.query_params.get('user_id')
        if user_filter:
            queryset = queryset.filter(
                salary_report__user_id=user_filter
            )

        return queryset

    def get_serializer_class(self):
        if self.action == 'create':
            return SalaryTransactionCreateSerializer
        return SalaryTransactionSerializer

    @db_transaction.atomic
    def create(self, request, *args, **kwargs):
        user = request.user
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        salary_report = SalaryReport.objects.select_for_update().get(
            id=serializer.validated_data['salary_report_id']
        )

        # Permission Check: Only Owner or Admin can create transactions for their staff
        if not user.is_superuser:
            if not user.is_owner or salary_report.user.hierarchy.owner != user:
                return Response(
                    {"detail": "You do not have permission to record payments for this report."},
                    status=status.HTTP_403_FORBIDDEN
                )

        amount_paid = serializer.validated_data['amount_paid']

        existing_transaction = SalaryTransaction.objects.filter(
            salary_report=salary_report
        ).order_by('-created_at').first()

        if existing_transaction and existing_transaction.status in ['PENDING', 'PROCESSING']:
            existing_transaction.status = 'SUCCESS'
            existing_transaction.amount_paid = amount_paid
            existing_transaction.payment_method = serializer.validated_data['payment_method']
            existing_transaction.payment_reference = serializer.validated_data.get('payment_reference', '')
            existing_transaction.note = serializer.validated_data.get('note', '')
            existing_transaction.processed_at = timezone.localtime()
            existing_transaction.save()
        else:
            SalaryTransaction.objects.create(
                salary_report=salary_report,
                amount_paid=amount_paid,
                payment_method=serializer.validated_data['payment_method'],
                payment_reference=serializer.validated_data.get('payment_reference', ''),
                note=serializer.validated_data.get('note', ''),
                processed_at=timezone.localtime(),
                status='SUCCESS',
            )

        # Update SalaryReport totals
        paid_amount = SalaryTransaction.objects.filter(
            salary_report=salary_report,
            status='SUCCESS'
        ).aggregate(total=Sum('amount_paid'))['total'] or 0

        salary_report.paid_amount = paid_amount
        salary_report.remaining_payment = salary_report.total_payable_amount - paid_amount
        salary_report.advance_amount += max(paid_amount - salary_report.total_payable_amount, 0)
        salary_report.save(
            update_fields=['paid_amount', 'remaining_payment', 'advance_amount', 'updated_at']
        )

        return Response(
            {'detail': 'Payment paid successfully.'},
            status=status.HTTP_200_OK
        )

