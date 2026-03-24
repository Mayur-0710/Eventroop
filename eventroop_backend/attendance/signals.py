from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.core.cache import cache
from decimal import Decimal
from .models import Attendance, AttendanceReport
from payroll.models import SalaryStructure, SalaryReport
from .utils import AttendanceCalculator
from payroll.utils import SalaryCalculator


def get_period_type(user, attendance_date):
    """Helper to get period type for a user and date."""
    salary_structure = (
        SalaryStructure.objects
        .filter(
            user=user,
            effective_from__lte=attendance_date,
            change_type__in=["BASE_SALARY", "INCREMENT"]
        )
        .order_by("-effective_from")
        .first()
    )
    return salary_structure.salary_type if salary_structure else "MONTHLY"


def update_attendance_report(user, attendance_date):
    """Extract common attendance report update logic."""
    try:
        period_type = get_period_type(user, attendance_date)
        calculator = AttendanceCalculator(user, base_date=attendance_date)

        report = calculator.get_attendance_report(
            base_date=attendance_date,
            period_type=period_type
        )

        if report:
            AttendanceReport.objects.update_or_create(
                user=user,
                start_date=report["start_date"],
                end_date=report["end_date"],
                period_type=report["period_type"],
                defaults={
                    "present_days": Decimal(str(report.get("present_days", 0))),
                    "absent_days": Decimal(str(report.get("absent_days", 0))),
                    "half_day_count": Decimal(str(report.get("half_day_count", 0))),
                    "paid_leave_days": Decimal(str(report.get("paid_leave_days", 0))),
                    "weekly_Offs": Decimal(str(report.get("weekly_Offs", 0))),
                    "unpaid_leaves": Decimal(str(report.get("unpaid_leaves", 0))),
                    "total_payable_days": Decimal(str(report.get("total_payable_days", 0))),
                    "total_payable_hours": Decimal(str(report.get("total_payable_hours", 0))),
                }
            )
        return report
    except Exception as e:
        print(f"Error updating attendance report: {e}")
        return None


def refresh_all_salary_reports(user):
    """Trigger a full recalculation of all salary reports for the user."""
    try:
        calculator = SalaryCalculator(user=user)
        calculator.refresh_salary_reports()
    except Exception as e:
        print(f"Error refreshing salary reports: {e}")


def clear_cache(user_id):
    """Clear attendance cache for user."""
    cache_key = f"attendance_reports_{user_id}"
    cache.delete(cache_key)


@receiver(post_save, sender=Attendance)
def update_attendance_report_on_save(sender, instance, created, **kwargs):
    """When attendance is created or updated, recalculate and save the report."""
    user = instance.user
    attendance_date = instance.date

    update_attendance_report(user, attendance_date)
    clear_cache(user.id)


@receiver(post_save, sender=AttendanceReport)
def create_or_update_salary_report_on_attendance(sender, instance, created, **kwargs):
    """Auto-recalculate ALL salary reports when any AttendanceReport changes."""
    refresh_all_salary_reports(instance.user)