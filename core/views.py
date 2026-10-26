import csv
import datetime as dt
import io
import tempfile

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.management import call_command
from django.core.paginator import Paginator
from django.db import DatabaseError
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import appsettings, dates, i18n, licence, subscription_api
from . import demo as demo_service
from .exports import csv_response
from .forms import GeneralForm, LicenceForm, SecurityForm, SubscriptionForm, ThemeForm
from .i18n import ui
from .middleware import DEMO_SESSION_KEY, LANG_SESSION_KEY
from .models import ActivityLog, AppSettings, FontChoice, Label, ThemePreset, UserPreference

staff_required = user_passes_test(lambda u: u.is_active and u.is_staff)


def _back(request, fallback="/"):
    nxt = request.POST.get("next") or request.GET.get("next") or request.META.get("HTTP_REFERER") or fallback
    if not url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        nxt = fallback
    return redirect(nxt)


@require_POST
def set_language(request):
    lang = request.POST.get("language", "en")
    if lang in i18n.LANG_CODES:
        request.session[LANG_SESSION_KEY] = lang
        if request.user.is_authenticated:
            UserPreference.objects.update_or_create(user=request.user, defaults={"language": lang})
    return _back(request)


# ---------------------------------------------------------------------------
# Labels & translations
# ---------------------------------------------------------------------------
@login_required
@staff_required
def labels(request):
    qs = Label.objects.all()
    q, group = request.GET.get("q", "").strip(), request.GET.get("group", "")
    if q:
        qs = qs.filter(
            Q(key__icontains=q)
            | Q(default__icontains=q)
            | Q(en__icontains=q)
            | Q(ml__icontains=q)
            | Q(hi__icontains=q)
            | Q(ta__icontains=q)
        )
    if group:
        qs = qs.filter(group=group)
    if request.GET.get("missing"):
        qs = qs.filter(Q(ml="") | Q(hi="") | Q(ta=""))

    if request.method == "POST":
        ids = [int(i) for i in request.POST.getlist("ids") if i.isdigit()]
        changed = []
        for lbl in Label.objects.filter(pk__in=ids):
            if request.POST.get(f"reset_{lbl.pk}"):
                lbl.en = lbl.default
                changed.append(lbl)
                continue
            dirty = False
            for code in i18n.LANG_CODES:
                value = request.POST.get(f"{code}_{lbl.pk}")
                if value is not None and value.strip() != getattr(lbl, code):
                    setattr(lbl, code, value.strip())
                    dirty = True
            if dirty:
                changed.append(lbl)
        for lbl in changed:
            if not lbl.en:
                lbl.en = lbl.default
            lbl.save()
        i18n.refresh(force=True)
        messages.success(request, ui("{n} labels saved.", n=len(changed)))
        return redirect(request.get_full_path())

    page = Paginator(qs, 40).get_page(request.GET.get("page"))
    return render(
        request,
        "core/labels.html",
        {
            "page": page,
            "q": q,
            "group": group,
            "groups": Label.objects.order_by("group").values_list("group", flat=True).distinct(),
            "languages": i18n.LANGUAGES,
        },
    )


@login_required
@staff_required
def labels_export(request):
    resp, w = csv_response(f"labels-{timezone.localdate()}.csv")
    w.writerow(["key", "group", "default", *i18n.LANG_CODES])
    w.writerows(Label.objects.values_list("key", "group", "default", *i18n.LANG_CODES).iterator(chunk_size=1000))
    return resp


@login_required
@staff_required
@require_POST
def labels_import(request):
    upload = request.FILES.get("file")
    if not upload:
        messages.error(request, ui("Choose a CSV file first."))
        return redirect("core:labels")
    try:
        rows = list(csv.DictReader(io.StringIO(upload.read().decode("utf-8-sig"))))
    except (UnicodeDecodeError, csv.Error):
        messages.error(request, ui("This file could not be read. Use a CSV exported from this page."))
        return redirect("core:labels")
    existing = {lbl.key: lbl for lbl in Label.objects.filter(key__in=[r.get("key") for r in rows])}
    changed = []
    for r in rows:
        lbl = existing.get(r.get("key"))
        if not lbl:
            continue
        dirty = False
        for code in i18n.LANG_CODES:
            if code in r and r[code] is not None and r[code].strip() != getattr(lbl, code):
                setattr(lbl, code, r[code].strip())
                dirty = True
        if dirty:
            lbl.en = lbl.en or lbl.default
            lbl.updated_at = timezone.now()
            changed.append(lbl)
    Label.objects.bulk_update(changed, [*i18n.LANG_CODES, "updated_at"], batch_size=500)
    i18n.refresh(force=True)
    messages.success(request, ui("{n} labels updated from the file.", n=len(changed)))
    return redirect("core:labels")


# ---------------------------------------------------------------------------
# Demo mode
# ---------------------------------------------------------------------------
@login_required
def demo(request):
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "enable":
                generated = demo_service.enable()
                request.session[DEMO_SESSION_KEY] = True
                messages.success(request, ui("Demo mode is on. You are now looking at sample data."))
                if generated:
                    messages.info(request, ui("Sample data was created for the demo."))
                return redirect("dashboard")
            if action == "disable":
                request.session.pop(DEMO_SESSION_KEY, None)
                messages.success(request, ui("Back to your own data."))
                return redirect("dashboard")
            if action == "regenerate" and request.user.is_staff:
                demo_service.generate(reset=True)
                request.session[DEMO_SESSION_KEY] = True
                messages.success(request, ui("Fresh sample data created."))
                return redirect("dashboard")
        except DatabaseError as exc:
            messages.error(request, ui("The demo database could not be prepared: {error}", error=str(exc)[:300]))
        return redirect("core:demo")
    return render(request, "core/demo.html", {"demo_ready": demo_service.has_data()})


# ---------------------------------------------------------------------------
# Per-user text size
# ---------------------------------------------------------------------------
@login_required
@require_POST
def text_size(request):
    try:
        size = int(request.POST.get("size", 100))
    except ValueError:
        size = 100
    if 80 <= size <= 150:
        UserPreference.objects.update_or_create(user=request.user, defaults={"text_size": size})
    return _back(request)


# ---------------------------------------------------------------------------
# App Settings (administrators only)
# ---------------------------------------------------------------------------
admin_required = user_passes_test(lambda u: u.is_active and u.is_superuser)
SETTINGS_TABS = ("general", "theme", "subscription", "security", "activity")


@login_required
@admin_required
def app_settings(request):
    app = AppSettings.objects.select_related("font", "theme_preset").get(pk=request.app_settings.pk)
    tab = request.POST.get("tab") or request.GET.get("tab") or "general"
    tab = tab if tab in SETTINGS_TABS else "general"
    forms_ = {
        "general": GeneralForm(instance=app),
        "theme": ThemeForm(instance=app),
        "security": SecurityForm(instance=app),
        "subscription": SubscriptionForm(instance=app),
        "licence": LicenceForm(install_id=app.install_id),
    }
    if request.method == "POST" and tab in forms_:
        action = request.POST.get("action", "save")
        if tab == "subscription" and action == "check":
            ok, message = subscription_api.check(app)
            (messages.success if ok else messages.error)(request, message)
            appsettings.forget()
            return redirect(f"{request.path}?tab={tab}")
        data, files = request.POST, request.FILES
        key = "licence" if tab == "subscription" and action == "licence" else tab
        form = {
            "general": lambda: GeneralForm(data, files, instance=app),
            "theme": lambda: ThemeForm(data, instance=app),
            "security": lambda: SecurityForm(data, instance=app),
            "subscription": lambda: SubscriptionForm(data, instance=app),
            "licence": lambda: LicenceForm(data, install_id=app.install_id),
        }[key]()
        forms_[key] = form
        if form.is_valid():
            if key == "licence":
                # a signed key from the supplier sets the date (and user limit) without typing it
                p = form.payload
                app.licence_key = form.cleaned_data["licence_key"]
                app.sub_valid_until = dt.date.fromisoformat(p["u"])
                app.sub_max_users, app.sub_plan = int(p.get("m", 0)), p.get("p", "")[:60]
                app.save(update_fields=["licence_key", "sub_valid_until", "sub_max_users", "sub_plan", "updated_at"])
                messages.success(request, ui("Licence saved. Valid until {date}.", date=dates.fmt(app.sub_valid_until)))
            else:
                hours_before = app.work_hours_per_day
                form.save()
                messages.success(request, ui("Settings saved."))
                if key == "general" and app.work_hours_per_day != hours_before:
                    _rework_harvests()
                if key == "subscription" and app.sub_api_enabled:
                    ok, message = subscription_api.check(app)  # show at once whether the URL works
                    (messages.success if ok else messages.warning)(request, message)
            appsettings.forget()
            return redirect(f"{request.path}?tab={tab}")

    status = licence.status(app)
    for form in forms_.values():
        for name in form.errors:
            if name in form.fields:
                form.fields[name].widget.attrs["class"] = (
                    form.fields[name].widget.attrs.get("class", "") + " is-invalid"
                )
    ctx = {
        "tab": tab,
        "forms": forms_,
        "app": app,
        "status": status,
        "presets": ThemePreset.objects.all(),
        "tab_list": [
            ("general", "bi-house-gear", ui("Estate profile")),
            ("theme", "bi-palette", ui("Theme")),
            ("subscription", "bi-patch-check", ui("Subscription")),
            ("security", "bi-shield-lock", ui("Security & backup")),
            ("activity", "bi-clock-history", ui("Activity log")),
        ],
        "state_label": {
            "trial": ui("Trial"),
            "active": ui("Active"),
            "expiring": ui("Ending soon"),
            "grace": ui("Payment pending"),
            "expired": ui("Read-only"),
        }[status.state],
        "action_labels": {"add": ui("Added"), "change": ui("Changed"), "delete": ui("Deleted")},
        "font_stacks": {str(f.pk): f.css_stack for f in FontChoice.objects.all()},
    }
    if tab == "subscription":
        from .auth import plan_users

        ctx["users_used"] = plan_users().count()
        ctx["manual_allowed"] = settings.SUBSCRIPTION_MANUAL_ALLOWED
    if tab == "activity":
        logs = ActivityLog.objects.select_related("user")
        if request.GET.get("user", "").isdigit():
            logs = logs.filter(user_id=int(request.GET["user"]))
        if request.GET.get("action") in ("add", "change", "delete"):
            logs = logs.filter(action=request.GET["action"])
        q = request.GET.get("q", "").strip()
        if q:
            logs = logs.filter(Q(summary__icontains=q) | Q(model__icontains=q))
        ctx["logs"] = Paginator(logs, 50).get_page(request.GET.get("page"))
        ctx["log_users"] = get_user_model().objects.filter(activitylog__isnull=False).distinct().order_by("username")
    return render(request, "core/app_settings.html", ctx)


@login_required
@admin_required
def backup(request):
    """Download all real data (not the demo data) as one JSON file; restore with `manage.py loaddata`."""
    # written to a temporary file and streamed from there: a big estate's data never sits in memory twice
    raw = tempfile.TemporaryFile()  # noqa: SIM115 - closed by FileResponse
    out = io.TextIOWrapper(raw, encoding="utf-8", write_through=True)
    call_command(
        "dumpdata",
        "estate",
        "core",
        "auth.user",
        "auth.group",
        database="default",
        indent=1,
        natural_foreign=True,
        stdout=out,
        exclude=["core.activitylog"],
    )
    out.flush()
    out.detach()  # keep the file open for the response
    raw.seek(0)
    return FileResponse(
        raw, as_attachment=True, filename=f"pms-backup-{timezone.localdate()}.json", content_type="application/json"
    )


def _rework_harvests():
    """The hours in a full day changed: harvest per person / per hour is worked out again (real and demo)."""
    from estate import productivity

    productivity.refresh_all("default")
    if demo_service.has_data():
        productivity.refresh_all("demo")


def logo(request):
    app = request.app_settings
    if not app.logo:
        raise Http404
    resp = HttpResponse(bytes(app.logo), content_type=app.logo_type or "image/png")
    resp["Cache-Control"] = "private, max-age=3600"
    resp["X-Content-Type-Options"] = "nosniff"
    return resp


def mobile_server_error(request):
    """Phone app only: an error page with the details to send to Mentors Soft Tech (also kept in
    pms-errors.log in the app's data folder). Plain HTML: templates or the database may be what failed."""
    import html
    import sys
    import traceback

    from django.http import HttpResponseServerError

    details = "".join(traceback.format_exception(*sys.exc_info())) if sys.exc_info()[0] else ""
    return HttpResponseServerError(
        "<!doctype html><html><head><meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Error</title></head><body style='font-family:system-ui,sans-serif;padding:16px'>"
        "<h3>Something went wrong</h3><p>Go back and try again. If it happens again, take a screenshot of "
        "this page and send it to Mentors Soft Tech.</p><p><a href='/'>Back to the app</a></p>"
        f"<pre style='white-space:pre-wrap;font-size:11px;background:#f4f4f4;padding:10px'>{html.escape(details)}</pre>"
        "</body></html>"
    )
