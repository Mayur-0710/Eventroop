from decimal import Decimal

from django.db.models import OuterRef, Subquery, Value, DecimalField, ExpressionWrapper, F
from django.db.models.functions import Coalesce
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from collections import defaultdict

from accounts.models import CustomUser
from attendance.models import AttendanceReport,Attendance
from payroll.models import  SalaryStructure, SalaryReport, SalaryTransaction

from .serializers import EmployeeSalaryAnalysisSerializer
from .filters import (
    build_employee_filters,
    build_period_filters,
    build_sort,
    ALLOWED_EMPLOYEE_SORT_FIELDS,
    AttendanceAnalysisFilter
)

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

from rest_framework.pagination import PageNumberPagination


# ── Pagination classes ──────────────────────────────────────────────────────

class AttendancePagination(PageNumberPagination):
    page_size              = 10        # default records per page
    page_size_query_param  = 'page_size'
    max_page_size          = 100
    page_query_param       = 'page'

    def get_paginated_response(self, data, extra_meta=None):
        """Extend default response with extra_meta (totals, group_by, etc.)"""
        payload = {
            'pagination': {
                'total_records': self.page.paginator.count,
                'total_pages':   self.page.paginator.num_pages,
                'current_page':  self.page.number,
                'page_size':     self.get_page_size(self.request),
                'next':          self.get_next_link(),
                'previous':      self.get_previous_link(),
            },
        }
        if extra_meta:
            payload.update(extra_meta)
        payload['data'] = data
        return Response(payload)

# ── Main View ───────────────────────────────────────────────────────────────

class AttendanceAnalysisAPIView(APIView):
    """
    GET /api/attendance/analysis/

    Query Params:
        -- Filters --
        date_from, date_to, date, month, year, week
        status_code, status_label, status_id
        user_id, employee_id, user_type, category, city, name_search

        -- Grouping & Ordering --
        group_by : employee (default) | date | status | month
        ordering : date | -date | user__first_name | status__code

        -- Pagination --
        page      : page number (default 1)
        page_size : records per page (default 10, max 100)
    """


    # ── Scope queryset by role ──────────────────────────────────
    def _base_queryset(self, request):
        qs = (
            Attendance.objects
            .select_related('user', 'status')
            .only(
                'date',
                'user__id', 'user__employee_id',
                'user__first_name', 'user__last_name',
                'user__user_type', 'user__category',
                'status__code', 'status__label',
            )
        )
        user = request.user
        if user.user_type == 'MASTER_ADMIN':
            return qs
        if user.user_type == 'VSRE_OWNER':
            return qs.filter(user__created_by=user)
        if user.user_type in ('VSRE_MANAGER', 'LINE_MANAGER'):
            return qs.filter(user__created_by=user)
        return qs.filter(user=user)

    def get(self, request, *args, **kwargs):
        qs = self._base_queryset(request)

        # Apply filters
        filterset = AttendanceAnalysisFilter(request.GET, queryset=qs, request=request)
        if not filterset.is_valid():
            return Response(filterset.errors, status=status.HTTP_400_BAD_REQUEST)
        qs = filterset.qs

        # Apply ordering
        ordering = request.GET.get('ordering', '-date')
        allowed  = {'date', '-date', 'user__first_name', '-user__first_name', 'status__code', '-status__code'}
        if ordering in allowed:
            qs = qs.order_by(ordering)

        group_by = request.GET.get('group_by', 'employee')
        paginator = AttendancePagination()

        if group_by == 'month':
            return self._paginate_grouped(request, qs, paginator, self._group_by_month, 'month')
        if group_by == 'date':
            return self._paginate_grouped(request, qs, paginator, self._group_by_date, 'date')
        if group_by == 'status':
            return self._paginate_grouped(request, qs, paginator, self._group_by_status, 'status')

        return self._paginate_grouped(request, qs, paginator, self._group_by_employee, 'employee')

    # ── Pagination wrapper for grouped data ─────────────────────
    def _paginate_grouped(self, request, qs, paginator, group_fn, group_by_label):
        """
        Groups the full queryset first, then paginates the grouped list.
        This ensures page_size = N groups (employees/dates/statuses), not N raw rows.
        """
        grouped_list = group_fn(qs)                          # list of dicts
        paginated    = paginator.paginate_queryset(grouped_list, request)
        return paginator.get_paginated_response(
            data       = paginated,
            extra_meta = {
                'group_by':    group_by_label,
                'total_groups': len(grouped_list),
            }
        )

    # ── Grouping helpers ────────────────────────────────────────

    def _group_by_month(self, qs) -> list:
        """
        Groups by YEAR-MONTH → then by each DATE inside that month.
        Each month entry contains:
        - year, month, month_label
        - total_days   : distinct dates recorded
        - status_summary : {PRESENT: N, ABSENT: N, …} across all employees for the month
        - days         : [{ date, total, records: [{employee_id, name, status_code, status_label}] }]
        """
        from collections import defaultdict
        import calendar

        # month_key  → date_key → list of records
        month_map = defaultdict(lambda: defaultdict(list))

        for att in qs:
            month_key = (att.date.year, att.date.month)   # (2025, 1)
            date_key  = str(att.date)                      # "2025-01-15"
            month_map[month_key][date_key].append({
                'employee_id':  att.user.employee_id or str(att.user.id),
                'name':         att.user.get_full_name(),
                'status_code':  att.status.code,
                'status_label': att.status.label,
            })

        result = []
        for (year, month), date_map in sorted(month_map.items(), reverse=True):
            # Build per-date list
            days = [
                {
                    'date':    d,
                    'total':   len(records),
                    'records': records,
                }
                for d, records in sorted(date_map.items())
            ]

            # Aggregate status summary across the whole month
            status_summary = defaultdict(int)
            for day in days:
                for rec in day['records']:
                    status_summary[rec['status_code']] += 1

            result.append({
                'year':           year,
                'month':          month,
                'month_label':    calendar.month_name[month],   # "January"
                'month_key':      f"{year}-{month:02d}",        # "2025-01"
                'total_days':     len(days),
                'status_summary': dict(status_summary),
                'days':           days,
            })

        return result
    
    def _group_by_employee(self, qs) -> list:
        grouped = defaultdict(lambda: {'records': [], 'summary': defaultdict(int)})

        for att in qs:
            key = att.user.employee_id or str(att.user.id)
            grouped[key].setdefault('name', att.user.get_full_name())
            grouped[key].setdefault('employee_id', key)
            grouped[key]['records'].append({
                'date':         str(att.date),
                'status_code':  att.status.code,
                'status_label': att.status.label,
            })
            grouped[key]['summary'][att.status.code] += 1

        return [
            {
                'employee_id':   emp_id,
                'name':          info['name'],
                'total_records': len(info['records']),
                'summary':       dict(info['summary']),
                'attendance':    info['records'],
            }
            for emp_id, info in grouped.items()
        ]

    def _group_by_date(self, qs) -> list:
        grouped = defaultdict(list)
        for att in qs:
            grouped[str(att.date)].append({
                'employee_id':  att.user.employee_id or str(att.user.id),
                'name':         att.user.get_full_name(),
                'status_code':  att.status.code,
                'status_label': att.status.label,
            })
        return [
            {'date': d, 'total': len(records), 'records': records}
            for d, records in sorted(grouped.items(), reverse=True)
        ]

    def _group_by_status(self, qs) -> list:
        grouped = defaultdict(list)
        for att in qs:
            grouped[att.status.code].append({
                'date':        str(att.date),
                'employee_id': att.user.employee_id or str(att.user.id),
                'name':        att.user.get_full_name(),
            })
        return [
            {'status_code': code, 'total': len(records), 'records': records}
            for code, records in grouped.items()
        ]