
from django.db.models.signals import post_save
from django.dispatch import receiver
from accounts.models import CustomUser
from .models import Wallet

@receiver(post_save, sender=CustomUser)
def create_wallet_for_user(sender, instance, created, **kwargs):
    """Auto-create wallet when a new user is created"""
    if created:
        Wallet.objects.get_or_create(user=instance)