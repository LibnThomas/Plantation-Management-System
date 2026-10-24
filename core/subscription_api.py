"""
Fetches the subscription's "valid till" date from the vendor's URL (App Settings → Subscription).

Request:   GET <API URL>?install_id=<installation ID>&estate=<estate name>
           headers: Accept: application/json, Authorization: Bearer <token> (when a token is set)
Answer:    JSON such as {"valid_until": "2027-03-31", "max_users": 10, "plan": "Standard"}
           (the date field name is configurable, dotted paths like data.valid_until work),
           or just the date as plain text. Dates: YYYY-MM-DD or DD-MM-YYYY.

When it runs: on the first request after the app (server process) starts, then again every
"Check again every (hours)", and when the administrator presses "Check now".
If the URL can't be reached, the last date received stays in force - a network problem never
locks anyone out, and blocking the URL doesn't extend the subscription either.
"""

import datetime as dt
import json
import logging
import threading
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.db import connections
from django.utils import timezone

from . import dates
from .i18n import N_, ui

log = logging.getLogger(__name__)
MAX_BYTES = 64 * 1024
_lock = threading.Lock()
_started = {"done": False}  # one check per server process start, whatever the interval


class ApiError(Exception):
    pass


def parse_date(value):
    value = str(value or "").strip()[:10]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d/%m/%y"):
        try:
            return dt.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ApiError(N_("The answer has no valid date."))


def _dig(data, path):
    for part in path.split("."):
        if not isinstance(data, dict) or part not in data:
            raise ApiError(N_("The answer has no '{field}' field."))
        data = data[part]
    return data


def _parent(data, path):
    """The JSON object that holds the date field (max_users / plan are read next to it)."""
    *head, _ = path.split(".")
    for part in head:
        data = data.get(part, {}) if isinstance(data, dict) else {}
    return data if isinstance(data, dict) else {}


def locked():
    """Vendor-controlled installation (SUBSCRIPTION_MANUAL_ALLOWED=False): the API is always on and its
    URL / token come from the server settings, so the administrator can't point it elsewhere."""
    return not settings.SUBSCRIPTION_MANUAL_ALLOWED


def api_on(app):
    return locked() or app.sub_api_enabled


def api_url(app):
    url = settings.SUBSCRIPTION_API_URL if locked() else (app.sub_api_url or settings.SUBSCRIPTION_API_URL)
    return (url or "").strip()


def api_token(app):
    return settings.SUBSCRIPTION_API_TOKEN if locked() else (app.sub_api_token or settings.SUBSCRIPTION_API_TOKEN)


def fetch(app):
    """Calls the URL and returns {"valid_until": date, "max_users": int|None, "plan": str|None}."""
    url = api_url(app)
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ApiError(N_("The API URL must start with https:// (or http://)."))
    query = urllib.parse.urlencode({"install_id": app.install_id, "estate": app.estate_name})
    full = f"{url}{'&' if parts.query else '?'}{query}"
    headers = {"Accept": "application/json, text/plain", "User-Agent": "PlantationMS"}
    token = api_token(app)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(full, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=settings.SUBSCRIPTION_API_TIMEOUT) as resp:  # noqa: S310
            body = resp.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise ApiError(f"HTTP {e.code}") from None
    except Exception as e:  # DNS, refused, timeout, TLS ... (or no network at all)
        reason = getattr(e, "reason", e)
        raise ApiError(str(reason)[:200] or "connection failed") from None
    if len(body) > MAX_BYTES:
        raise ApiError(N_("The answer is too big."))
    text = body.decode("utf-8", errors="replace").strip()
    path = (app.sub_api_field or "valid_until").strip()
    try:
        data = json.loads(text)
    except ValueError:
        return {"valid_until": parse_date(text), "max_users": None, "plan": None}
    if not isinstance(data, dict):
        return {"valid_until": parse_date(data), "max_users": None, "plan": None}
    parent = _parent(data, path)
    users = parent.get("max_users")
    return {
        "valid_until": parse_date(_dig(data, path)),
        "max_users": int(users) if isinstance(users, int | str) and str(users).isdigit() else None,
        "plan": str(parent["plan"])[:60] if parent.get("plan") else None,
    }


def check(app):
    """Runs one check and saves the result on the settings row. Returns (ok, message)."""
    from .models import AppSettings

    _started["done"] = True
    now = timezone.now()
    changes = {"sub_api_checked_at": now}
    try:
        result = fetch(app)
    except ApiError as e:
        message = ui(str(e), field=app.sub_api_field)
        changes["sub_api_message"] = message[:300]
        ok = False
        log.warning("Subscription API check failed: %s", message)
    else:
        changes.update(sub_valid_until=result["valid_until"], sub_api_ok_at=now)
        if result["max_users"] is not None:
            changes["sub_max_users"] = result["max_users"]
        if result["plan"] is not None:
            changes["sub_plan"] = result["plan"]
        message = ui("Valid till {date} (from the API).", date=dates.fmt(result["valid_until"]))
        changes["sub_api_message"] = message[:300]
        ok = True
    AppSettings.objects.filter(pk=app.pk).update(**changes)
    for key, value in changes.items():
        setattr(app, key, value)
    return ok, message


def is_due(app):
    if not (api_on(app) and api_url(app) and app.pk):
        return False
    if not _started["done"] or not app.sub_api_checked_at:
        return True
    return timezone.now() - app.sub_api_checked_at >= dt.timedelta(hours=app.sub_api_interval_hours or 12)


def _run_in_background(pk):
    from .models import AppSettings

    try:
        app = AppSettings.objects.filter(pk=pk).first()
        if app:
            check(app)
    except Exception:  # never let the background check break the app
        log.exception("Subscription API check crashed")
    finally:
        connections.close_all()
        _lock.release()


def refresh_if_due(app):
    """Called on every request; starts a check when the app has just started or the interval has passed.
    The page never waits for it (it runs in the background), unless SUBSCRIPTION_API_SYNC is set (tests)."""
    if not is_due(app) or not _lock.acquire(blocking=False):
        return
    _started["done"] = True
    if settings.SUBSCRIPTION_API_SYNC:
        try:
            check(app)
        finally:
            _lock.release()
        return
    try:
        threading.Thread(target=_run_in_background, args=(app.pk,), daemon=True, name="pms-subscription-api").start()
    except RuntimeError:  # no threads available (e.g. the in-browser demo): check right here
        try:
            check(app)
        finally:
            _lock.release()
