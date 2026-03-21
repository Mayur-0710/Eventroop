from django.urls import path
from .views import SalaryAnalysisAPIView,AttendanceAnalysisAPIView

app_name = "analysis"

urlpatterns = [
    path("salary/", SalaryAnalysisAPIView.as_view(), name="salary-analysis"),
    path('attendance/', AttendanceAnalysisAPIView.as_view(), name='attendance-analysis'),

]