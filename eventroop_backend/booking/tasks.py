from celery import shared_task
from .models import PrimaryOrder, SecondaryOrder, TernaryOrder
from .utils import bulk_update_status

@shared_task
def update_statuses_by_time():
    return {
        "primary_updated":   bulk_update_status(PrimaryOrder.objects.all(),   PrimaryOrder),
        "secondary_updated": bulk_update_status(SecondaryOrder.objects.all(), SecondaryOrder),
        "ternary_updated":   bulk_update_status(TernaryOrder.objects.all(),   TernaryOrder),
    }