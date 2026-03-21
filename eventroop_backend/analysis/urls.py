from django.urls import path
from .views import SalaryAnalysisAPIView

app_name = "analysis"

urlpatterns = [
    path("salary/", SalaryAnalysisAPIView.as_view(), name="salary-analysis"),
]