from django.urls import path
from .views import SalaryAnalysisAPIView,UserAttendanceAPIView

app_name = "analysis"

urlpatterns = [
    path("salary/", SalaryAnalysisAPIView.as_view(), name="salary-analysis"),
    path('attendance/', UserAttendanceAPIView.as_view(), name='attendance-analysis'),

]