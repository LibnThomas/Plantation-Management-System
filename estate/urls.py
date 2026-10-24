from django.urls import path

from . import views

app_name = "estate"

urlpatterns = [
    path("attendance/daily/", views.attendance_bulk, name="attendance_bulk"),
    path("attendance/calendar/", views.attendance_calendar, name="attendance_calendar"),
    path("payroll/generate/", views.payroll_generate, name="payroll_generate"),
    path("payroll/<int:pk>/payslip/", views.payslip, name="payslip"),
    path("payroll/<int:pk>/payslip/pdf/", views.payslip_pdf, name="payslip_pdf"),
    path("payroll/payment/<int:pk>/", views.salary_paid_detail, name="salary_paid"),
    path("employees/<int:pk>/profile/", views.employee_profile, name="employee_profile"),
    path("lists/", views.lookup_index, name="lookups"),
    path("harvests/people/", views.harvest_people, name="harvest_people"),
    path("<slug:slug>/", views.crud_list, name="list"),
    path("<slug:slug>/add/", views.crud_form, name="create"),
    path("<slug:slug>/export/", views.crud_export_csv, name="export"),
    path("<slug:slug>/pdf/", views.crud_export_pdf, name="pdf"),
    path("<slug:slug>/<int:pk>/edit/", views.crud_form, name="update"),
    path("<slug:slug>/<int:pk>/delete/", views.crud_delete, name="delete"),
]
