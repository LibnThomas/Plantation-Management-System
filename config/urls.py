from django.conf import settings
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.contrib.staticfiles import views as static_views
from django.urls import include, path, re_path

from core import pwa
from core.auth import PlanAuthenticationForm
from reports import views as report_views

urlpatterns = [
    path("", report_views.dashboard, name="dashboard"),
    path("login/", auth_views.LoginView.as_view(authentication_form=PlanAuthenticationForm), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("password/", auth_views.PasswordChangeView.as_view(success_url="/"), name="password_change"),
    path("admin/", admin.site.urls),
    path("manifest.webmanifest", pwa.manifest, name="manifest"),
    path("sw.js", pwa.service_worker, name="service_worker"),
    path("favicon.ico", pwa.favicon),
    path("app/", pwa.install, name="install"),
    path("app/offline/", pwa.offline, name="offline"),
    path("app/android.apk", pwa.android_apk, name="android_apk"),
    path("settings/", include("core.urls")),
    path("reports/", include("reports.urls")),
    path("m/", include("estate.urls")),
]

if settings.MOBILE_APP:  # the phone app has no web server in front: Django serves the styles / scripts itself
    urlpatterns.append(re_path(r"^static/(?P<path>.*)$", static_views.serve, {"insecure": True}))

if settings.MOBILE_APP:  # the phone app: show what went wrong (only reachable from inside the phone)
    handler500 = "core.views.mobile_server_error"
