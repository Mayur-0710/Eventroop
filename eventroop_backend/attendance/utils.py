import calendar
from datetime import date, timedelta
from decimal import Decimal
from django.db.models import Count, Sum, Q
from .models import Attendance, AttendanceStatus,AttendanceReport


class AttendanceCalculator:

    def __init__(self, user, base_date=None):
        self.user = user
        self.base_date = base_date or date.today()
        self.status = self._load_statuses()

    # ---------------- Period Helpers ----------------
    def _get_period(self, base, period):
        if period in ("HOURLY", "DAILY"):
            return base, base

        if period == "WEEKLY":
            start = base - timedelta(days=base.weekday())
            return start, start + timedelta(days=6)

        if period == "FORTNIGHTLY":
            last = calendar.monthrange(base.year, base.month)[1]
            return (
                (base.replace(day=1), base.replace(day=15))
                if base.day <= 15
                else (base.replace(day=16), base.replace(day=last))
            )

        # MONTHLY
        first = base.replace(day=1)
        last = calendar.monthrange(base.year, base.month)[1]
        return first, base.replace(day=last)

    # ---------------- Status ----------------
    def _load_statuses(self):
        qs = AttendanceStatus.objects.filter(owner__is_superuser=True, is_active=True)

        return {
            k: qs.filter(code__icontains=k.upper()).first()
            for k in [
                "present",
                "absent",
                "paid_leave",
                "half_day",
                "weekly_off",
                "unpaid_leave",
            ]
        }

    # ---------------- Core Calculation ----------------
    def _aggregate(self, start, end):
        qs = Attendance.objects.filter(
            user=self.user,
            date__range=(start, end)
        )

        agg = qs.aggregate(
            present=Count("id", filter=Q(status=self.status["present"])),
            absent=Count("id", filter=Q(status=self.status["absent"])),
            paid_leave=Count("id", filter=Q(status=self.status["paid_leave"])),
            half_day=Count("id", filter=Q(status=self.status["half_day"])),
            weekly_off=Count("id", filter=Q(status=self.status["weekly_off"])),
            unpaid_leave=Count("id", filter=Q(status=self.status["unpaid_leave"])),
            duration=Sum("duration"),
        )

        hours = (
            Decimal(agg["duration"].total_seconds()) / 3600
            if agg["duration"] else Decimal("0")
        )

        payable_days = agg["present"] + agg["paid_leave"] + Decimal("0.5") * agg["half_day"]
        
        return {
            "present_days": agg["present"],
            "absent_days": agg["absent"],
            "half_day_count": agg["half_day"],
            "paid_leave_days": agg["paid_leave"],
            "weekly_Offs": agg["weekly_off"],
            "unpaid_leaves": agg["unpaid_leave"],
            "total_payable_days": float(payable_days),
            "total_payable_hours": float(hours),
        }
    
    # ---------------- Public APIs ----------------
    def get_attendance_report(self, base_date=None, period_type="MONTHLY"):
        base = base_date or self.base_date
        start, end = self._get_period(base, period_type)

        return {
            "start_date": start,
            "end_date": end,
            "period_type": period_type,
            **self._aggregate(start, end),
        }

    def get_attendance_for_date_range(self, start_date, end_date):
        return {
            "start_date": start_date,
            "end_date": end_date,
            **self._aggregate(start_date, end_date),
        }

    def get_all_periods_attendance(
        self,
        start_date=None,
        end_date=None,
        period_type="MONTHLY",
    ):
        first = (
            Attendance.objects
            .filter(user=self.user)
            .order_by("date")
            .only("date")
            .first()
        )

        if not first and not start_date:
            return []

        current = start_date or first.date
        end_date = end_date or date.today()

        reports_to_save = []

        while current <= end_date:
            start, end = self._get_period(current, period_type)

            report = self.get_attendance_report(start, period_type)

            reports_to_save.append(
                AttendanceReport(
                    user=self.user,
                    start_date=start,
                    end_date=end,
                    period_type=report["period_type"],
                    present_days=Decimal(report.get("present_days", 0)),
                    absent_days=Decimal(report.get("absent_days", 0)),
                    half_day_count=Decimal(report.get("half_day_count", 0)),
                    paid_leave_days=Decimal(report.get("paid_leave_days", 0)),
                    weekly_Offs=Decimal(report.get("weekly_Offs", 0)),
                    unpaid_leaves=Decimal(report.get("unpaid_leaves", 0)),
                    total_payable_days=Decimal(report.get("total_payable_days", 0)),
                    total_payable_hours=Decimal(report.get("total_payable_hours", 0)),
                )
            )

            # Move to next period
            if period_type == "MONTHLY":
                if start.month == 12:
                    current = date(start.year + 1, 1, 1)
                else:
                    current = date(start.year, start.month + 1, 1)
            else:
                current = end + timedelta(days=1)

        # 🔥 Bulk upsert (Postgres / Django 4.1+)
        AttendanceReport.objects.bulk_create(
            reports_to_save,
            update_conflicts=True,
            unique_fields=["user", "start_date", "end_date", "period_type"],
            update_fields=[
                "present_days",
                "absent_days",
                "half_day_count",
                "paid_leave_days",
                "weekly_Offs",
                "unpaid_leaves",
                "total_payable_days",
                "total_payable_hours",
            ],
        )

        return reports_to_save
