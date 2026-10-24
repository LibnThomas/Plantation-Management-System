from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("language/", views.set_language, name="set_language"),
    path("labels/", views.labels, name="labels"),
    path("labels/export/", views.labels_export, name="labels_export"),
    path("labels/import/", views.labels_import, name="labels_import"),
    path("demo/", views.demo, name="demo"),
    path("text-size/", views.text_size, name="text_size"),
    path("app/", views.app_settings, name="app_settings"),
    path("app/backup/", views.backup, name="backup"),
    path("logo/", views.logo, name="logo"),
]
