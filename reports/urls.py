from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("analysis/", views.analysis, name="analysis"),
    path("builder/", views.builder, name="builder"),
    path("builder/save/", views.save_report, name="save"),
    path("builder/<int:pk>/delete/", views.delete_report, name="delete_saved"),
]
