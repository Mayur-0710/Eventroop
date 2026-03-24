import calendar
from collections import defaultdict
from decimal import Decimal

from django.db.models import (
    DecimalField,
    ExpressionWrapper,
    F,
    OuterRef,
    Subquery,
    Value,
)
from django.db.models.functions import Coalesce
from rest_framework import status
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.views import APIView

from attendance.models import  AttendanceReport
from payroll.models import SalaryReport, SalaryStructure

from .filters import (
    ALLOWED_EMPLOYEE_SORT_FIELDS,
    build_employee_filters,
    build_period_filters,
    build_sort,
)
from .mixins import PermissionScopeMixin
from .serializers import EmployeeSalaryAnalysisSerializer, UserAttendanceSerializer

ZERO = Decimal("0.00")
_ZERO_FIELD = Value(
    Decimal("0.00"),
    output_field=DecimalField(max_digits=12, decimal_places=2),
)


class SalaryAnalysisAPIView(PermissionScopeMixin, APIView):
    """
    Returns paginated salary analysis per employee, with a summary block.

    Permission scoping (via PermissionScopeMixin):
        - Superuser  → all employees
        - Owner      → employees in their hierarchy
        - Staff/Mgr  → only themselves
    """

    pagination_class = api_settings.DEFAULT_PAGINATION_CLASS

    # ------------------------------------------------------------------
    # Subquery / annotation builders
    # ------------------------------------------------------------------

    @staticmethod
    def _build_annotations():
        attendance_base = AttendanceReport.objects.filter(
            user=OuterRef("user_id"),
            start_date=OuterRef("start_date"),
            end_date=OuterRef("end_date"),
        )

        final_salary_sq = Subquery(
            SalaryStructure.objects.filter(
                user=OuterRef("user_id"),
                change_type__in=["BASE_SALARY", "INCREMENT"],
                effective_from__lte=OuterRef("start_date"),
            )
            .order_by("-effective_from")
            .values("final_salary")[:1]
        )

        absent_days_sq    = Subquery(attendance_base.values("absent_days")[:1])
        unpaid_leaves_sq  = Subquery(attendance_base.values("unpaid_leaves")[:1])
        half_day_count_sq = Subquery(attendance_base.values("half_day_count")[:1])

        total_unpaid_days = ExpressionWrapper(
            Coalesce(absent_days_sq, _ZERO_FIELD)
            + Coalesce(unpaid_leaves_sq, _ZERO_FIELD)
            + Decimal("0.5") * Coalesce(half_day_count_sq, _ZERO_FIELD),
            output_field=DecimalField(max_digits=10, decimal_places=2),
        )

        excess_balance = ExpressionWrapper(
            F("paid_amount") - F("total_payable_amount"),
            output_field=DecimalField(max_digits=12, decimal_places=2),
        )

        return {
            "total_payable_days": Coalesce(
                Subquery(attendance_base.values("total_payable_days")), _ZERO_FIELD
            ),
            "total_unpaid_days": total_unpaid_days,
            "salary": Coalesce(final_salary_sq, _ZERO_FIELD),
            "excess_balance": excess_balance,
        }

    # ------------------------------------------------------------------
    # Summary helper
    # ------------------------------------------------------------------

    @staticmethod
    def _build_summary(reports):
        """
        Single pass over the already-filtered, annotated report list.
        Returns totals and overall attendance percentage.
        """
        total_salary        = ZERO
        total_paid          = ZERO
        total_excess        = ZERO
        total_payable_days  = 0
        total_calendar_days = 0

        for report in reports:
            total_salary       += report.total_payable_amount or ZERO
            total_paid         += report.paid_amount          or ZERO
            total_excess       += report.excess_balance       or ZERO
            total_payable_days += int(report.total_payable_days or 0)
            total_calendar_days += int(
                (report.end_date - report.start_date).days + 1 or 0
            )

        total_attendance_pct = (
            round((total_payable_days / total_calendar_days) * 100, 2)
            if total_calendar_days > 0
            else None
        )

        return {
            "totals": {
                "total_salary":         total_salary,
                "total_amount_paid":    total_paid,
                "total_excess_balance": total_excess,
            },
            "attendance": {
                "total_days_present":   total_payable_days,
                "total_attendance_pct": total_attendance_pct,
            },
        }

    # ------------------------------------------------------------------
    # Payment status helper
    # ------------------------------------------------------------------

    @staticmethod
    def _payment_status(report):
        excess = report.excess_balance
        paid   = report.paid_amount

        if excess >= 0:
            return "Paid"
        if paid == 0:
            return "Unpaid"
        if paid > 0 and excess < 0:
            return "Partially Paid"
        return None

    # ------------------------------------------------------------------
    # Main handler
    # ------------------------------------------------------------------

    def get(self, request):
        params = request.query_params
        errors = {}

        emp_q,    emp_errors    = build_employee_filters(params)
        period_q, period_errors = build_period_filters(params)
        order_by, sort_errors   = build_sort(params)

        if emp_errors:
            errors["employee_filters"] = emp_errors
        if period_errors:
            errors["period_filters"] = period_errors
        if sort_errors:
            errors["sort"] = sort_errors

        if errors:
            return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)

        # ── Sort levels ──────────────────────────────────────────────────
        emp_sort_fields   = set(ALLOWED_EMPLOYEE_SORT_FIELDS.values())
        employee_order_by = [o for o in order_by if o.lstrip("-") in emp_sort_fields] or ["id"]
        period_order_by   = [o for o in order_by if o.lstrip("-") not in emp_sort_fields] or ["-start_date"]

        # ── Scoped + paginated employee queryset ─────────────────────────
        employee_qs = (
            self.get_user_queryset(request)   # permission-scoped
            .filter(emp_q)
            .order_by(*employee_order_by)
        )

        paginator      = self.pagination_class()
        paginated_emps = paginator.paginate_queryset(employee_qs, request, view=self)
        employee_ids   = [e.id for e in paginated_emps]

        # ── SalaryReport queryset ────────────────────────────────────────
        all_reports = list(
            SalaryReport.objects.filter(user_id__in=employee_ids)
            .filter(period_q)
            .select_related("user")
            .annotate(**self._build_annotations())
            .order_by("user_id", *period_order_by)
        )

        # ── Group reports by employee ────────────────────────────────────
        periods_by_user: dict[int, list] = {e.id: [] for e in paginated_emps}

        for report in all_reports:
            calendar_days = (report.end_date - report.start_date).days + 1
            periods_by_user[report.user_id].append(
                {
                    "start_date":         report.start_date,
                    "end_date":           report.end_date,
                    "calendar_days":      calendar_days,
                    "attendance_pct":     round(
                        (report.total_payable_days / calendar_days) * 100, 2
                    ),
                    "total_payable_days": report.total_payable_days,
                    "total_unpaid_days":  report.total_unpaid_days,
                    "salary":             report.salary,
                    "total_salary":       report.total_payable_amount or ZERO,
                    "payment_status":     self._payment_status(report),
                    "amount_paid":        report.paid_amount or ZERO,
                    "excess_balance":     report.excess_balance or ZERO,
                }
            )

        # ── Serialize ────────────────────────────────────────────────────
        results = [
            {
                "id":            emp.id,
                "emp_id":        emp.employee_id,
                "first_name":    emp.first_name,
                "middle_name":   emp.middle_name,
                "last_name":     emp.last_name,
                "mobile_number": emp.mobile_number,
                "status":        emp.is_active,
                "periods":       periods_by_user.get(emp.id, []),
            }
            for emp in paginated_emps
        ]

        serializer = EmployeeSalaryAnalysisSerializer(results, many=True)
        response   = paginator.get_paginated_response(serializer.data)
        response.data["summary"] = self._build_summary(all_reports)
        return response


# ---------------------------------------------------------------------------


class UserAttendanceAPIView(PermissionScopeMixin, APIView):
    """
    Retrieve attendance data for users.

    Permission scoping (via PermissionScopeMixin):
        - Superuser  → all users / all attendance
        - Owner      → users in their hierarchy
        - Staff/Mgr  → only themselves

    Behavior:
        - If `user_id` is provided → single user (no pagination).
          Returns 404 if the requested user is outside the requester's scope.
        - If `user_id` is not provided → paginated list of scoped users.

    Query Parameters:
        user_id     (int,  optional) – specific user ID
        start_month (str,  optional) – YYYY-MM  (inclusive)
        end_month   (str,  optional) – YYYY-MM  (inclusive)
        page        (int,  optional) – page number
        page_size   (int,  optional) – items per page
    """

    pagination_class = api_settings.DEFAULT_PAGINATION_CLASS

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def get(self, request):
        user_id     = request.query_params.get("user_id")
        start_month = request.query_params.get("start_month")
        end_month   = request.query_params.get("end_month")

        if user_id:
            return self._get_single_user(request, user_id, start_month, end_month)
        return self._get_all_users(request, start_month, end_month)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_single_user(self, request, user_id, start_month, end_month):
        # Scope the lookup — prevents accessing users outside the requester's scope.
        try:
            user = self.get_user_queryset(request).get(pk=user_id)
        except Exception:
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)

        qs = (
            self.get_attendance_queryset(request)
            .filter(user=user)
            .select_related("status")
        )
        qs = self._apply_date_filters(qs, start_month, end_month)

        data = {
            "user":       user.id,
            "attendance": self._build_attendance(qs).get(user.id, {}),
        }
        return Response(UserAttendanceSerializer(data).data)

    def _get_all_users(self, request, start_month, end_month):
        user_qs = self.get_user_queryset(request).order_by("id")  # permission-scoped

        paginator       = self.pagination_class()
        paginated_users = paginator.paginate_queryset(user_qs, request, view=self)
        paginated_ids   = [u.id for u in paginated_users]

        qs = (
            self.get_attendance_queryset(request)   # permission-scoped
            .filter(user__id__in=paginated_ids)
            .select_related("user", "status")
        )
        qs = self._apply_date_filters(qs, start_month, end_month)

        attendance_map = self._build_attendance(qs)

        result = [
            {
                "user":       user_id,
                "attendance": attendance_map.get(user_id, {}),
            }
            for user_id in paginated_ids
        ]

        serializer = UserAttendanceSerializer(result, many=True)
        return paginator.get_paginated_response(serializer.data)

    # ------------------------------------------------------------------
    # Date filtering
    # ------------------------------------------------------------------

    def _apply_date_filters(self, qs, start_month, end_month):
        if start_month:
            year, month = map(int, start_month.split("-"))
            qs = qs.filter(date__gte=f"{year}-{month:02d}-01")

        if end_month:
            year, month = map(int, end_month.split("-"))
            last_day = calendar.monthrange(year, month)[1]
            qs = qs.filter(date__lte=f"{year}-{month:02d}-{last_day}")

        return qs

    # ------------------------------------------------------------------
    # Attendance grouping
    # ------------------------------------------------------------------

    def _build_attendance(self, qs):
        """
        Group queryset into:
            { user_id -> { 'Mon-YYYY' -> { 'DD': 'P/A/H/PL/UL' } } }
        Both months and days are sorted chronologically.
        """
        grouped = defaultdict(lambda: defaultdict(dict))

        for record in qs:
            month_key = record.date.strftime("%b-%Y")
            day_key   = record.date.strftime("%d")
            grouped[record.user_id][month_key][day_key] = self._map_status(
                record.status.label
            )

        return {
            user_id: {
                month: dict(sorted(days.items()))
                for month, days in sorted(
                    months.items(),
                    key=lambda x: self._month_sort_key(x[0]),
                )
            }
            for user_id, months in grouped.items()
        }

    @staticmethod
    def _map_status(label: str) -> str:
        mapping = {
            "Present":      "P",
            "Absent":       "A",
            "Half Day":     "H",
            "Paid Leave":   "PL",
            "Unpaid Leave": "UL",
        }
        return mapping.get(label, label[0].upper() if label else "?")

    @staticmethod
    def _month_sort_key(month_str: str):
        from datetime import datetime
        return datetime.strptime(month_str, "%b-%Y")