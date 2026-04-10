from rest_framework.routers import DefaultRouter
from django.urls import path, include
from . import views

app_name = 'wallet'

router = DefaultRouter()
router.register(r'wallet-payment-service', views.WalletPaymentService, basename='wallet-payment-service')


urlpatterns = router.urls