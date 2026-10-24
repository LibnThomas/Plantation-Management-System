import time

from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.http import url_has_allowed_host_and_scheme

from . import appsettings, audit, i18n, licence, subscription_api
from .dbrouter import DEFAULT_DB, DEMO_DB, reset_active_db, set_active_db

DEMO_SESSION_KEY = "pms_demo"
LANG_SESSION_KEY = "pms_lang"
ACTIVITY_SESSION_KEY = "pms_last_activity"


def user_preference(request):
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        return getattr(user, "pms_preference", None)
    return None


class PMSMiddleware:
    """Activates the user's language, the App Settings and the data set (real or demo) for this request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        pref = user_preference(request)
        lang = pref.language if pref else request.session.get(LANG_SESSION_KEY, "en")
        if lang not in i18n.LANG_CODES:
            lang = "en"
        translation.activate(lang)
        request.LANGUAGE_CODE = lang
        request.text_size = pref.text_size if pref else 100
        i18n.refresh()
        request.app_settings = appsettings.load()

        request.demo_mode = bool(request.session.get(DEMO_SESSION_KEY))
        token = set_active_db(DEMO_DB if request.demo_mode else DEFAULT_DB)
        user_token = audit.set_user(getattr(request, "user", None))
        try:
            response = self.get_response(request)
        finally:
            audit.reset_user(user_token)
            reset_active_db(token)
        response.headers.setdefault("Content-Language", lang)
        return response


class AccessMiddleware:
    """
    Subscription and session rules (runs after the message middleware):
    - logs a user out after the inactivity time set in App Settings,
    - after the licence (plus grace days) runs out the app becomes read-only: pages, reports and
      exports still open, but nothing can be saved until the valid till date is renewed.
    """

    # POSTs that must keep working in read-only mode
    ALWAYS_ALLOWED = ("login", "logout", "core:set_language", "core:text_size", "core:app_settings")

    def __init__(self, get_response):
        self.get_response = get_response
        self._allowed = None

    def allowed_paths(self):
        if self._allowed is None:
            self._allowed = {reverse(name) for name in self.ALWAYS_ALLOWED}
        return self._allowed

    def __call__(self, request):
        app = request.app_settings
        self._remember_day(app)
        subscription_api.refresh_if_due(app)  # first request after start, then every few hours
        request.licence = licence.status(app)

        user = request.user
        if user.is_authenticated and app.session_timeout_minutes:
            now, last = time.time(), request.session.get(ACTIVITY_SESSION_KEY)
            if last and now - last > app.session_timeout_minutes * 60:
                logout(request)
                messages.info(request, i18n.ui("You were logged out after a period of inactivity."))
                return redirect(f"{reverse('login')}?next={request.path}")
            if not last or now - last > 60:  # no session write on every request
                request.session[ACTIVITY_SESSION_KEY] = now

        if request.licence.read_only and request.method == "POST" and request.path not in self.allowed_paths():
            messages.error(
                request,
                i18n.ui("The subscription has ended, so the app is read-only. Ask your administrator to renew it."),
            )
            back = request.META.get("HTTP_REFERER") or "/"
            if not url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}):
                back = "/"
            return redirect(back)
        return self.get_response(request)

    @staticmethod
    def _remember_day(app):
        """First day of use (trial start), install ID and the last day the app was used (clock check)."""
        today = timezone.localdate()
        changes = {}
        if not app.install_id:
            changes["install_id"] = licence.new_install_id()
        if not app.first_run_date:
            changes["first_run_date"] = today
        if not app.last_seen_date or today > app.last_seen_date:
            changes["last_seen_date"] = today
        if changes and app.pk:
            type(app).objects.filter(pk=app.pk).update(**changes)
            for key, value in changes.items():
                setattr(app, key, value)
