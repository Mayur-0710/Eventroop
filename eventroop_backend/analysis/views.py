import calendar
from collections import defaultdict
from decimal import Decimal
from datetime import date

from django.db.models import (
    DecimalField,
    ExpressionWrapper,
    F,
    OuterRef,
    Subquery,
    Value,
    Count,
    Sum,
    Q
)
from django.db.models.functions import Coalesce,TruncMonth,TruncDay
from rest_framework import status,viewsets
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.views import APIView
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError

from attendance.models import  AttendanceReport
from payroll.models import SalaryReport, SalaryStructure
from booking.models import SecondaryOrder,TernaryOrder, TotalInvoice,Payment,PaymentMethod

from .filters import (
    ALLOWED_EMPLOYEE_SORT_FIELDS,
    build_employee_filters,
    build_period_filters,
    build_sort,
)
from .mixins import PermissionScopeMixin
from .serializers import (
    EmployeeSalaryAnalysisSerializer,
    UserAttendanceSerializer,
    MonthlyAnalyticsResponseSerializer,
    )



ZERO = Decimal("0.00")
_ZERO_FIELD = Value(
    Decimal("0.00"),
    output_field=DecimalField(max_digits=12, decimal_places=2),
)

class SalaryAnalysisAPIView(PermissionScopeMixin, APIView):
    """
    Returns paginated salary analysis per employee with period-wise breakdown.

    Query Params:
    - Employee filters:
        user_id, user_type, emp_id, emp_id__icontains,
        first_name__icontains, last_name__icontains,
        mobile_number, search, status (active|inactive|all)

    - Period filters:
        start_date, start_date__gte, start_date__lte,
        end_date, end_date__gte, end_date__lte,
        days_present__gte/lte, days_absent__gte/lte,
        salary__gte/lte, total_salary__gte/lte,
        amount_paid__gte/lte

    - Sorting:
        sort_by (comma-separated), sort_dir (asc|desc)

    Computed:
    - total_unpaid_days = absent + unpaid_leaves + (0.5 * half_days)
    - excess_balance = paid_amount - total_payable_amount

    Returns:
    - Paginated employees with nested periods + summary
    
    Permission scoping (via PermissionScopeMixin):
        - Superuser  → all employees
        - Owner      → employees in their hierarchy
        - Staff/Mgr  → only themselves

    Returns paginated salary analysis per employee with period-wise breakdown.

    Features:
    - Employee + period level filtering
    - Sorting support
    - Attendance & salary annotations
    - Summary of totals and attendance

    Computed fields:
    - total_payable_days: from AttendanceReport
    - total_unpaid_days: absent + unpaid_leaves + (0.5 * half_days)
    - salary: latest from SalaryStructure
    - excess_balance: paid_amount - total_payable_amount

    Permissions:
    - Superuser: all employees
    - Owner: hierarchy employees
    - Others: self only

    Response:
    - Paginated employees with nested periods
    - Includes overall summary block
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
            attendance_pct = round((report.total_payable_days / calendar_days) * 100, 2)
            periods_by_user[report.user_id].append(
                {
                    "start_date":         report.start_date,
                    "end_date":           report.end_date,
                    "calendar_days":      calendar_days,
                    "attendance_pct":     attendance_pct,
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
        user_id     (int,  optional) - specific user ID
        start_month (str,  optional) - YYYY-MM  (inclusive)
        end_month   (str,  optional) - YYYY-MM  (inclusive)
        page        (int,  optional) - page number
        page_size   (int,  optional) - items per page
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

class PaymentMasterViewSet(viewsets.ViewSet):
    """
    ViewSet for monthly performance across invoices, bookings, and payments.

    Endpoints:
        GET /api/month-wise-performance/
        GET /api/month-wise-performance/?year=2024&month=3
        GET /api/daily-collection/?year=2025&month=3
        GET /api/payment-mode-analytics/?year=2025&month=12
    """

    DIGITAL_METHODS = {PaymentMethod.UPI, PaymentMethod.CARD, PaymentMethod.BANK}
    CASH_METHODS    = {PaymentMethod.CASH}
    CHEQUE_METHODS  = {PaymentMethod.CHEQUE}

    # ── Filter parsers ─────────────────────────────────────────────────────────

    def _parse_filters(self, request):
        """Parse optional year/month query params. Month=0 means all months."""
        year_param  = request.query_params.get("year")
        month_param = request.query_params.get("month")

        year = int(year_param) if year_param else date.today().year

        if month_param:
            try:
                month = int(month_param)
            except ValueError:
                raise ValidationError("Month must be a valid integer between 1 and 12.")
            if not 1 <= month <= 12:
                raise ValidationError("Month must be between 1 and 12.")
        else:
            month = 0

        return year, month

    def _parse_daily_filters(self, request):
        """Parse required year/month for day-level granularity."""
        try:
            year  = int(request.query_params.get("year",  date.today().year))
            month = int(request.query_params.get("month", date.today().month))
        except ValueError:
            raise ValidationError("Year and month must be valid integers.")

        if not 1 <= month <= 12:
            raise ValidationError("Month must be between 1 and 12.")

        return year, month

    # ── Shared queryset helper ─────────────────────────────────────────────────

    def _base_payment_qs(self, year, month):
        qs = Payment.objects.filter(paid_date__year=year)
        if month:
            qs = qs.filter(paid_date__month=month)
        return qs

    # ── month-wise-performance ─────────────────────────────────────────────────
    @action(detail=False, methods=["get"], url_path="month-wise-performance")
    def month_wise_performance(self, request):
        try:
            year, month   = self._parse_filters(request)
            monthly_data  = self._fetch_monthly_data(year, month)
            rows          = self._build_rows(monthly_data)
            response_data = {
                "year":    year,
                "month":   month,
                "rows":    rows,
                "summary": self._build_summary(rows),
            }
            return Response(MonthlyAnalyticsResponseSerializer(response_data).data)
        except ValidationError:
            raise
        except Exception as e:
            raise ValidationError(f"An unexpected error occurred: {e}")

    def _fetch_monthly_data(self, year, month):
        invoice_data       = self._fetch_invoice_data(year, month)
        secondary_bookings = self._fetch_order_bookings(SecondaryOrder, year, month)
        ternary_bookings   = self._fetch_order_bookings(TernaryOrder,   year, month)
        payment_data       = self._fetch_payment_data(year, month)
        return self._merge_monthly_data(invoice_data, secondary_bookings, ternary_bookings, payment_data)

    def _fetch_invoice_data(self, year, month):
        qs = TotalInvoice.objects.filter(period_start__year=year)
        if month:
            qs = qs.filter(period_start__month=month)
        return (
            qs
            .annotate(month=TruncMonth("period_start"))
            .values("month")
            .annotate(
                invoice_value    = Sum("total_amount", default=ZERO),
                pending_invoices = Count("id", filter=~Q(status="PAID")),
                total_invoices   = Count("id"),
            )
            .order_by("month")
        )

    def _fetch_payment_data(self, year, month):
        return (
            self._base_payment_qs(year, month)
            .annotate(month=TruncMonth("paid_date"))
            .values("month")
            .annotate(amt_collected=Sum("amount", default=ZERO))
            .order_by("month")
        )

    def _fetch_order_bookings(self, model, year, month):
        """Generic booking count fetcher for SecondaryOrder / TernaryOrder."""
        qs = model.objects.filter(start_datetime__year=year)
        if month:
            qs = qs.filter(start_datetime__month=month)
        return (
            qs
            .annotate(month=TruncMonth("start_datetime"))
            .values("month")
            .annotate(count=Count("id"))
            .order_by("month")
        )

    def _merge_monthly_data(self, invoice_data, secondary_bookings, ternary_bookings, payment_data):
        monthly_map = defaultdict(lambda: {
            "bookings":        0,
            "invoice_value":   ZERO,
            "amt_collected":   ZERO,
            "pending_invoices": 0,
            "total_invoices":  0,
        })

        for row in invoice_data:
            monthly_map[row["month"]].update({
                "invoice_value":    row["invoice_value"]    or ZERO,
                "pending_invoices": row["pending_invoices"],
                "total_invoices":   row["total_invoices"],
            })

        for row in (*secondary_bookings, *ternary_bookings):
            monthly_map[row["month"]]["bookings"] += row["count"]

        for row in payment_data:
            monthly_map[row["month"]]["amt_collected"] = row["amt_collected"] or ZERO

        return monthly_map

    def _build_rows(self, monthly_data):
        return [
            self._build_row(month, monthly_data[month])
            for month in sorted(monthly_data.keys(), reverse=True)
        ]

    def _build_row(self, month, data):
        invoice_value = data["invoice_value"]
        amt_collected = data["amt_collected"]
        return {
            "month":                  month.strftime("%b'%Y") if month else "All Months",
            "total_bookings":         data["bookings"],
            "invoice_value":          str(invoice_value),
            "amt_collected":          str(amt_collected),
            "balance":                str(invoice_value - amt_collected),
            "collection_pct":         self._collection_pct(amt_collected, invoice_value),
            "pending_invoices":       data["pending_invoices"],
            "invoices_not_generated": max(data["bookings"] - data["total_invoices"], 0),
        }

    def _build_summary(self, rows):
        if not rows:
            return {
                "total_bookings": 0, "total_invoice_value": "0.00",
                "total_amt_collected": "0.00", "total_balance": "0.00",
                "collection_pct": None, "total_pending_invoices": 0,
                "total_invoices_not_generated": 0,
            }

        total_invoice   = sum(Decimal(r["invoice_value"])  for r in rows)
        total_collected = sum(Decimal(r["amt_collected"])   for r in rows)

        return {
            "total_bookings":             sum(int(r["total_bookings"])         for r in rows),
            "total_invoice_value":        str(total_invoice),
            "total_amt_collected":        str(total_collected),
            "total_balance":              str(total_invoice - total_collected),
            "collection_pct":             self._collection_pct(total_collected, total_invoice),
            "total_pending_invoices":     sum(int(r["pending_invoices"])       for r in rows),
            "total_invoices_not_generated": sum(int(r["invoices_not_generated"]) for r in rows),
        }

    # ── daily-collection ───────────────────────────────────────────────────────

    @action(detail=False, methods=["get"], url_path="daily-collection")
    def daily_collection(self, request):
        try:
            year, month = self._parse_daily_filters(request)
            return Response({
                "year":             year,
                "month":            month,
                "daily_collection": self._fetch_daily_collection_map(year, month),
            })
        except ValidationError:
            raise
        except Exception as e:
            raise ValidationError(f"An unexpected error occurred: {e}")

    def _fetch_daily_collection_map(self, year, month):
        """
        Returns attendance-style format:
        { "Mar-2025": { "01": "500.00", "15": "1200.00" } }
        Only days with actual collection are included.
        """
        rows = (
            self._base_payment_qs(year, month)
            .annotate(day=TruncDay("paid_date"))
            .values("day")
            .annotate(amt_collected=Sum("amount", default=ZERO))
            .order_by("day")
        )

        daily_payment = defaultdict(dict)
        for row in rows:
            day: date = row["day"]
            daily_payment[day.strftime("%b-%Y")][day.strftime("%d")] = str(row["amt_collected"])

        return dict(daily_payment)

    # ── payment-mode-analytics ─────────────────────────────────────────────────

    @action(detail=False, methods=["get"], url_path="payment-mode-analytics")
    def payment_mode_analytics(self, request):
        """
        Returns:
          - summary_cards : digital / cash / cheque totals + grand total
          - mode_split    : per-method amount + % of total
          - monthly_trend : month-wise breakdown by method + row total
        """
        try:
            year, month = self._parse_filters(request)
            return Response(self._fetch_payment_mode_data(year, month))
        except ValidationError:
            raise
        except Exception as e:
            raise ValidationError(f"An unexpected error occurred: {e}")

    def _fetch_payment_mode_data(self, year, month):
        qs = self._base_payment_qs(year, month)

        method_totals = qs.values("method").annotate(total=Sum("amount", default=ZERO))
        method_map    = {row["method"]: row["total"] for row in method_totals}
        grand_total   = sum(method_map.values()) or ZERO

        def pct(amount):
            return round(float(amount / grand_total * 100), 1) if grand_total > 0 else 0.0

        mode_split = [
            {
                "method": method,
                "amount": str(method_map.get(method, ZERO)),
                "pct":    pct(method_map.get(method, ZERO)),
            }
            for method in PaymentMethod.values
        ]

        def group_total(methods):
            return sum(method_map.get(m, ZERO) for m in methods)

        digital_total = group_total(self.DIGITAL_METHODS)
        cash_total    = group_total(self.CASH_METHODS)
        cheque_total  = group_total(self.CHEQUE_METHODS)

        summary_cards = {
            "digital_payments": {"amount": str(digital_total), "pct_of_total": pct(digital_total)},
            "cash_payments":    {"amount": str(cash_total),    "pct_of_total": pct(cash_total)},
            "cheque_payments":  {"amount": str(cheque_total),  "pct_of_total": pct(cheque_total)},
            "total_collected": {
                "amount": str(grand_total),
                "period": f"{calendar.month_abbr[month]} {year}" if month else str(year),
            },
        }

        monthly_raw = (
            qs
            .annotate(month=TruncMonth("paid_date"))
            .values("month", "method")
            .annotate(total=Sum("amount", default=ZERO))
            .order_by("month", "method")
        )

        trend_map = defaultdict(lambda: defaultdict(Decimal))
        for row in monthly_raw:
            trend_map[row["month"]][row["method"]] += row["total"]

        monthly_trend = [
            {
                "month":   month_dt.strftime("%b'%Y"),
                "methods": {m: str(trend_map[month_dt].get(m, ZERO)) for m in PaymentMethod.values},
                "total":   str(sum(trend_map[month_dt].values())),
            }
            for month_dt in sorted(trend_map)
        ]

        return {
            "year":          year,
            "month":         month,
            "summary_cards": summary_cards,
            "mode_split":    mode_split,
            "monthly_trend": monthly_trend,
        }

    # ── Utility ────────────────────────────────────────────────────────────────

    @staticmethod
    def _collection_pct(collected, total):
        if total and total > 0:
            return round((collected / total) * 100, 1)
        return None