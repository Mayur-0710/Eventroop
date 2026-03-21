from decimal import Decimal

from django.db.models import OuterRef, Subquery, Value, DecimalField, ExpressionWrapper, F
from django.db.models.functions import Coalesce
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated


from accounts.models import CustomUser
from attendance.models import AttendanceReport
from payroll.models import  SalaryStructure, SalaryReport, SalaryTransaction
from .serializers import EmployeeSalaryAnalysisSerializer
from .filters import build_employee_filters, build_period_filters, build_sort, ALLOWED_EMPLOYEE_SORT_FIELDS


ZERO = Value(Decimal("0.00"), output_field=DecimalField(max_digits=12, decimal_places=2))


class SalaryAnalysisAPIView(APIView):
    """
    GET /api/analysis/salary/

    Retrieve salary periods grouped by employee, with support for
    advanced filtering, sorting, and pagination.

    ────────────────────────────────────────────
    EMPLOYEE-LEVEL FILTERS
    ────────────────────────────────────────────
    user_id                : Exact employee primary key
    user_type              : VSRE_MANAGER | LINE_MANAGER | VSRE_STAFF
    emp_id                 : Exact employee ID
    emp_id__icontains      : Partial match on employee ID
    first_name__icontains  : Partial match on first name
    last_name__icontains   : Partial match on last name
    mobile_number          : Exact mobile number
    search                 : Searches across first_name, last_name,
                            emp_id, and mobile_number

    ────────────────────────────────────────────
    PERIOD-LEVEL FILTERS
    ────────────────────────────────────────────
    start_date             : Exact date (YYYY-MM-DD)
    start_date__gte        : Start date ≥ given value
    start_date__lte        : Start date ≤ given value
    end_date               : Exact date (YYYY-MM-DD)
    end_date__gte          : End date ≥ given value
    end_date__lte          : End date ≤ given value

    days_present__gte/lte  : Filter by days present
    days_absent__gte/lte   : Filter by days absent
    base_salary__gte/lte   : Filter by base salary
    salary__gte/lte        : Filter by salary
    total_salary__gte/lte  : Filter by total salary
    amount_paid__gte/lte   : Filter by amount paid

    status                 : PENDING | PROCESSING | SUCCESS |
                            FAILED | CANCELLED

    ────────────────────────────────────────────
    SORTING
    ────────────────────────────────────────────
    sort_by                : Comma-separated fields
                            (e.g., sort_by=start_date,salary)
    sort_dir               : asc (default) | desc
                            Applies to all sort fields

    ────────────────────────────────────────────
    PAGINATION (EMPLOYEE LEVEL)
    ────────────────────────────────────────────
    page                   : Page number (default: 1)
    page_size              : Results per page (default: 20, max: 100)

    ────────────────────────────────────────────
    SORTABLE FIELDS
    ────────────────────────────────────────────
    Employee fields:
        emp_id, first_name, last_name,
        mobile_number, user_type

    Period fields:
        start_date, end_date, calendar_days,
        days_present, days_absent,
        base_salary, salary, total_salary,
        amount_paid, excess_balance,
        status, payment_date
    """

    permission_classes = [IsAuthenticated]
    ALLOWED_TYPES = ["VSRE_MANAGER", "LINE_MANAGER", "VSRE_STAFF"]

    # ------------------------------------------------------------------
    # Subquery builders
    # ------------------------------------------------------------------

    @staticmethod
    def _build_annotations():
        """Returns dict of annotations to apply on SalaryReport queryset."""

        attendance_base = AttendanceReport.objects.filter(
            user=OuterRef("user_id"),
            start_date=OuterRef("start_date"),
            end_date=OuterRef("end_date"),
        )

        base_salary_sq = Subquery(
            SalaryStructure.objects.filter(
                user=OuterRef("user_id"),
                change_type="BASE_SALARY",
                effective_from__lte=OuterRef("start_date"),
            ).order_by("-effective_from").values("amount")[:1]
        )

        final_salary_sq = Subquery(
            SalaryStructure.objects.filter(
                user=OuterRef("user_id"),
                change_type__in=["BASE_SALARY", "INCREMENT"],
                effective_from__lte=OuterRef("start_date"),
            ).order_by("-effective_from").values("final_salary")[:1]
        )

        latest_tx = SalaryTransaction.objects.filter(
            salary_report=OuterRef("pk"),
        ).order_by("-created_at")

        return {
            "days_present": Coalesce(Subquery(attendance_base.values("present_days")[:1]), ZERO),
            "days_absent":  Coalesce(Subquery(attendance_base.values("absent_days")[:1]),  ZERO),
            "base_salary":  Coalesce(base_salary_sq,  ZERO),
            "salary":       Coalesce(final_salary_sq, ZERO),
            "tx_status":    Subquery(latest_tx.values("status")[:1]),
            "payment_date": Subquery(latest_tx.values("processed_at")[:1]),
            # excess_balance = paid_amount - total_payable_amount
            "excess_balance": ExpressionWrapper(
                F("paid_amount") - F("total_payable_amount"),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
        }

    # ------------------------------------------------------------------
    # Main handler
    # ------------------------------------------------------------------

    def get(self, request):
        params = request.query_params
        errors = {}

        # ---- pagination ----
        try:
            page = max(1, int(params.get("page", 1)))
        except (ValueError, TypeError):
            errors["page"] = "Must be a positive integer."
            page = 1

        try:
            page_size = min(100, max(1, int(params.get("page_size", 20))))
        except (ValueError, TypeError):
            errors["page_size"] = "Must be a positive integer (max 100)."
            page_size = 20

        # ---- filters ----
        emp_q, emp_filter_errors       = build_employee_filters(params)
        period_q, period_filter_errors = build_period_filters(params)

        # ---- sorting ----
        order_by, sort_errors = build_sort(params)

        all_errors = errors
        if emp_filter_errors:
            all_errors["employee_filters"] = emp_filter_errors
        if period_filter_errors:
            all_errors["period_filters"] = period_filter_errors
        if sort_errors:
            all_errors["sort"] = sort_errors

        if all_errors:
            return Response({"errors": all_errors}, status=status.HTTP_400_BAD_REQUEST)

        # ------------------------------------------------------------------
        # 1. Determine if sort is employee-level or period-level
        #    Employee-level sorts → apply on CustomUser queryset
        #    Period-level sorts   → apply on SalaryReport queryset
        # ------------------------------------------------------------------
        emp_sort_fields    = set(ALLOWED_EMPLOYEE_SORT_FIELDS.values())
        employee_order_by  = [o for o in order_by if o.lstrip("-") in emp_sort_fields]
        period_order_by    = [o for o in order_by if o.lstrip("-") not in emp_sort_fields]

        # Default ordering when nothing specified
        if not employee_order_by:
            employee_order_by = ["id"]
        if not period_order_by:
            period_order_by = ["-start_date"]

        # ------------------------------------------------------------------
        # 2. Employee queryset (paginated)
        # ------------------------------------------------------------------
        employees = (
            CustomUser.objects
            .filter(
                user_type__in=self.ALLOWED_TYPES,
                is_deleted=False,
            )
            .filter(emp_q)
            .order_by(*employee_order_by)
        )

        total_employees = employees.count()
        offset          = (page - 1) * page_size
        employees       = list(employees[offset: offset + page_size])
        employee_ids    = [e.id for e in employees]

        # ------------------------------------------------------------------
        # 3. SalaryReport queryset — filtered, annotated, sorted
        # ------------------------------------------------------------------
        all_reports = (
            SalaryReport.objects
            .filter(user_id__in=employee_ids)
            .filter(period_q)
            .select_related("user")
            .annotate(**self._build_annotations())
            .order_by("user_id", *period_order_by)
        )

        # ------------------------------------------------------------------
        # 4. Group reports by employee
        # ------------------------------------------------------------------
        periods_by_user: dict[int, list] = {e.id: [] for e in employees}

        for report in all_reports:
            total_salary   = report.total_payable_amount or Decimal("0.00")
            amount_paid    = report.paid_amount          or Decimal("0.00")
            excess_balance = (report.excess_balance      or Decimal("0.00"))

            periods_by_user[report.user_id].append({
                "start_date":     report.start_date,
                "end_date":       report.end_date,
                "calendar_days":  (report.end_date - report.start_date).days + 1,
                "days_present":   report.days_present,
                "days_absent":    report.days_absent,
                "base_salary":    report.base_salary,
                "salary":         report.salary,
                "total_salary":   total_salary,
                "status":         report.tx_status,
                "payment_date":   report.payment_date,
                "amount_paid":    amount_paid,
                "excess_balance": excess_balance,
            })

        # ------------------------------------------------------------------
        # 5. Build response
        # ------------------------------------------------------------------
        results = [
            {
                "emp_id":        emp.employee_id,
                "first_name":    emp.first_name,
                "middle_name":   emp.middle_name,
                "last_name":     emp.last_name,
                "mobile_number": emp.mobile_number,
                "periods":       periods_by_user.get(emp.id, []),
            }
            for emp in employees
        ]

        serializer = EmployeeSalaryAnalysisSerializer(results, many=True)

        return Response(
            {
                "meta": {
                    "total_employees": total_employees,
                    "page":            page,
                    "page_size":       page_size,
                    "total_pages":     -(-total_employees // page_size),
                    "applied_filters": {
                        k: v for k, v in params.items()
                        if k not in ("page", "page_size")
                    },
                },
                "results": serializer.data,
            },
            status=status.HTTP_200_OK,
        )