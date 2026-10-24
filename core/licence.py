"""
Subscription / licence.

A licence key is issued by the vendor (Mentors Soft Tech) with `python manage.py issue_licence` and the
vendor's private key, which never ships with the app. The app only holds the public key below, so
a key can be checked offline but cannot be made or changed by the customer.

    PMS1.<payload>.<signature>     payload = base64url(JSON), signature = Ed25519 over the payload

Payload: {"c": customer, "i": installation id or "*", "u": valid until (YYYY-MM-DD), "p": plan,
          "m": max users (0 = no limit), "g": grace days, "s": support contact, "n": issued on}

A key, when pasted in App Settings, fills the "valid till" date (and user limit); the date can also be
typed by the administrator or fetched from an API (see subscription_api.py). Without a date the
installation runs a free trial from the day it was first used.
Timeline:  active  ->  expiring (warning)  ->  grace (still full access)  ->  read-only.
Data is never deleted; renewing (pasting a new key) restores full access at once.
"""

import base64
import datetime as dt
import json
import uuid
from dataclasses import dataclass

from django.conf import settings
from django.utils import timezone

from .i18n import N_

# Public half of the vendor's signing key(s). Add a new one here to rotate keys.
VENDOR_PUBLIC_KEYS = ["zelayyOl7tAPVywTp8KgKfoyhydi/1BuasXwqcqCw0g="]
PREFIX = "PMS1"


class LicenceError(ValueError):
    pass


def _b64d(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _b64e(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def decode(key, install_id=None):
    """Checks the signature (and installation) and returns the payload dict. Raises LicenceError."""
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:
        raise LicenceError(N_("Licence keys cannot be checked: the 'cryptography' package is missing.")) from None

    try:
        prefix, body, sig = "".join(key.split()).split(".")
        if prefix != PREFIX:
            raise ValueError
        raw, signature = _b64d(body), _b64d(sig)
    except ValueError:
        raise LicenceError(N_("This is not a valid licence key.")) from None
    for pub in VENDOR_PUBLIC_KEYS:
        try:
            Ed25519PublicKey.from_public_bytes(base64.b64decode(pub)).verify(signature, body.encode())
            break
        except InvalidSignature:
            continue
    else:
        raise LicenceError(N_("This licence key was not issued for this application."))
    data = json.loads(raw)
    if install_id and data.get("i") not in ("*", install_id):
        raise LicenceError(N_("This licence key belongs to another installation."))
    try:
        dt.date.fromisoformat(data["u"])
    except (KeyError, ValueError):
        raise LicenceError(N_("This is not a valid licence key.")) from None
    return data


def issue(
    private_key_pem, customer, valid_until, install_id="*", plan="Standard", max_users=0, grace_days=None, support=""
):
    """Vendor side: makes a licence key (needs the private key)."""
    from cryptography.hazmat.primitives import serialization

    key = serialization.load_pem_private_key(private_key_pem, password=None)
    payload = {
        "c": customer,
        "i": install_id,
        "u": valid_until.isoformat(),
        "p": plan,
        "m": int(max_users),
        "g": settings.LICENCE_GRACE_DAYS if grace_days is None else int(grace_days),
        "s": support,
        "n": dt.date.today().isoformat(),
    }
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    return f"{PREFIX}.{body}.{_b64e(key.sign(body.encode()))}"


def new_install_id():
    raw = uuid.uuid4().hex[:12].upper()
    return "-".join(raw[i : i + 4] for i in range(0, 12, 4))


@dataclass
class Status:
    state: str  # trial / active / expiring / grace / expired
    valid_until: dt.date
    days_left: int  # until valid_until (negative after it)
    read_only_from: dt.date
    plan: str = ""
    max_users: int = 0
    support: str = ""
    is_trial: bool = False

    @property
    def read_only(self):
        return self.state == "expired"

    @property
    def show_banner(self):
        return self.state in ("expiring", "grace", "expired") or (self.is_trial and self.days_left <= 7)


def today_for(app):
    """Today, but never earlier than a day the app was already used (setting the clock back doesn't help)."""
    today = timezone.localdate()
    return max(today, app.last_seen_date) if app.last_seen_date else today


def status(app):
    """Where the app stands today. The valid till date comes from App Settings (typed by the
    administrator, filled from a licence key, or fetched from the API); without one: free trial."""
    today = today_for(app)
    if app.sub_valid_until:
        until, trial = app.sub_valid_until, False
    else:
        start = app.first_run_date or today
        until, trial = start + dt.timedelta(days=settings.LICENCE_TRIAL_DAYS - 1), True
    grace = settings.LICENCE_GRACE_DAYS
    days_left = (until - today).days
    read_only_from = until + dt.timedelta(days=grace + 1)
    if today >= read_only_from:
        state = "expired"
    elif days_left < 0:
        state = "grace"
    elif days_left < settings.LICENCE_WARN_DAYS:
        state = "expiring"
    else:
        state = "trial" if trial else "active"
    return Status(
        state=state,
        valid_until=until,
        days_left=days_left,
        read_only_from=read_only_from,
        plan=app.sub_plan,
        max_users=app.sub_max_users,
        support=settings.LICENCE_SUPPORT_CONTACT,
        is_trial=trial,
    )
