"""
Labels & translations.

Every visible text goes through `tr(key, default)`. Keys:
    ui:<English text>                 buttons, headings, messages (templates use {% t "..." %})
    field:<app.model>.<field>         field names
    help:<app.model>.<field>          help text under a field
    model:<app.model> / models:<...>  record name (singular / plural)
    module:<slug> / module_help:<slug> menu items and page intros

Rows live in core.Label (shared by real and demo data). Missing keys are created on first
use, pre-filled from translations.json (a starting translation memory the customer can edit).
"""

import json
import re
import threading
import time
from functools import lru_cache
from pathlib import Path

from django.db import DatabaseError
from django.db.models import Count, Max
from django.utils import timezone, translation

LANGUAGES = [("en", "English"), ("ml", "മലയാളം"), ("hi", "हिन्दी"), ("ta", "தமிழ்")]
LANG_CODES = [code for code, _ in LANGUAGES]
_LANG_INDEX = {code: i for i, code in enumerate(LANG_CODES)}
TRANSLATION_MEMORY = Path(__file__).with_name("translations.json")

_lock = threading.Lock()
_state = {"stamp": None, "rows": {}, "checked": 0.0}
REFRESH_SECONDS = 5  # how often a worker looks for label edits made by other workers


def current_lang():
    lang = (translation.get_language() or "en")[:2]
    return lang if lang in LANG_CODES else "en"


@lru_cache(maxsize=1)
def memory():
    """English text -> {"ml": .., "hi": .., "ta": ..} starting translations."""
    try:
        return json.loads(TRANSLATION_MEMORY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _label_model():
    from .models import Label

    return Label


def refresh(force=False):
    """Reload the label table when it changed. The check is one cheap aggregate query, made at most
    every few seconds (edits made in this worker refresh at once with force=True)."""
    now = time.monotonic()
    if not force and _state["stamp"] is not None and now - _state["checked"] < REFRESH_SECONDS:
        return
    _state["checked"] = now
    Label = _label_model()
    try:
        agg = Label.objects.aggregate(n=Count("id"), last=Max("updated_at"))
    except DatabaseError:
        return
    stamp = (agg["n"], agg["last"])
    if not force and stamp == _state["stamp"]:
        return
    rows = {r[0]: r[1:] for r in Label.objects.values_list("key", "en", "ml", "hi", "ta", "default")}
    with _lock:
        _state["rows"], _state["stamp"] = rows, stamp


def recheck():
    """Look for label changes on the next request (a label was saved in this worker)."""
    _state["checked"] = 0.0


def group_for(key):
    kind, _, rest = key.partition(":")
    return {
        "ui": "Screens & buttons",
        "module": "Menu",
        "module_help": "Menu",
        "menu": "Menu",
        "model": "Record names",
        "models": "Record names",
    }.get(kind) or (f"Fields: {rest.split('.')[1]}" if kind in ("field", "help") and rest.count(".") >= 2 else "Other")


def register(key, default, group=None):
    """Create the label row if it does not exist yet (pre-filled from the translation memory)."""
    Label = _label_model()
    mem = memory().get(default, {})
    try:
        obj, created = Label.objects.get_or_create(
            key=key[:255],
            defaults={
                "group": group or group_for(key),
                "default": default,
                "en": default,
                "ml": mem.get("ml", ""),
                "hi": mem.get("hi", ""),
                "ta": mem.get("ta", ""),
            },
        )
    except DatabaseError:
        return
    with _lock:
        _state["rows"][obj.key] = (obj.en, obj.ml, obj.hi, obj.ta, obj.default)


def tr(key, default="", **fmt):
    key = key[:255]  # same as the stored key, so long texts are found after the first use
    row = _state["rows"].get(key)
    if row is None:
        register(key, default)
        row = _state["rows"].get(key)
    text = default
    if row:
        text = row[_LANG_INDEX.get(current_lang(), 0)] or row[0] or row[4] or default
    if fmt:
        try:
            text = text.format(**fmt)
        except (KeyError, IndexError, ValueError):
            text = default.format(**fmt)
    return text


def ui(text, **fmt):
    return tr(f"ui:{text}", text, **fmt)


def N_(text):  # noqa: N802
    """Marks a long text kept in a variable as translatable (found by sync_labels); use ui() on it later."""
    return text


# ---------------------------------------------------------------------------
# Model / field helpers
# ---------------------------------------------------------------------------
def _cap(text):
    text = str(text)
    return text[:1].upper() + text[1:]


def field_label(model, name):
    f = model._meta.get_field(name)
    return tr(f"field:{model._meta.label_lower}.{name}", _cap(getattr(f, "verbose_name", name)))


def field_help(model, name):
    f = model._meta.get_field(name)
    help_text = str(getattr(f, "help_text", "") or "")
    return tr(f"help:{model._meta.label_lower}.{name}", help_text) if help_text else ""


def module_title(cfg):
    return tr(f"module:{cfg.slug}", cfg.title)


def model_label(model, plural=False):
    meta = model._meta
    if plural:
        return tr(f"models:{meta.label_lower}", _cap(meta.verbose_name_plural))
    return tr(f"model:{meta.label_lower}", _cap(meta.verbose_name))


# ---------------------------------------------------------------------------
# Sync: make sure every known text has a row (run after migrate / on demand)
# ---------------------------------------------------------------------------
CORE_MODELS = {"core.appsettings", "core.fontchoice", "core.themepreset", "core.activitylog"}
TEMPLATE_T = re.compile(r"""\{%\s*t\s+(["'])(.+?)\1""")
PY_UI = re.compile(r"""\b(?:ui|N_)\(\s*(["'])(.+?)\1""")


def all_known_labels():
    from django.apps import apps
    from django.conf import settings

    from estate.registry import MODULES

    out = {}
    for model in apps.get_models():
        if model._meta.app_label != "estate" and model._meta.label_lower not in CORE_MODELS:
            continue
        meta = model._meta
        out[f"model:{meta.label_lower}"] = _cap(meta.verbose_name)
        out[f"models:{meta.label_lower}"] = _cap(meta.verbose_name_plural)
        for f in meta.get_fields():
            if not getattr(f, "concrete", False):
                continue
            out[f"field:{meta.label_lower}.{f.name}"] = _cap(f.verbose_name)
            if getattr(f, "help_text", ""):
                out[f"help:{meta.label_lower}.{f.name}"] = str(f.help_text)
    from estate.registry import MENU_GROUPS

    for key, label in MENU_GROUPS:
        out[f"menu:{key}"] = label
    for cfg in MODULES:
        out[f"module:{cfg.slug}"] = cfg.title
        if cfg.help_text:
            out[f"module_help:{cfg.slug}"] = cfg.help_text
    from estate.payroll import SOURCES
    from reports.builder import PRESETS

    for text in [*SOURCES.values(), *(p[0] for p in PRESETS)]:
        out[f"ui:{text}"] = text
    base = Path(settings.BASE_DIR)
    for path in list(base.glob("templates/**/*.html")) + list(base.glob("*/templates/**/*.html")):
        for m in TEMPLATE_T.finditer(path.read_text(encoding="utf-8")):
            out[f"ui:{m.group(2)}"] = m.group(2)
    for path in base.glob("[!.]*/**/*.py"):
        if "migrations" in path.parts or "tests" in path.name:
            continue
        for m in PY_UI.finditer(path.read_text(encoding="utf-8")):
            out[f"ui:{m.group(2)}"] = m.group(2)
    return out


def sync_labels(fill_missing_translations=True):
    """Create rows for new texts; fill empty translations from the memory. Never overwrites edits."""
    Label = _label_model()
    known = all_known_labels()
    mem = memory()
    existing = {lbl.key: lbl for lbl in Label.objects.all()}
    new, changed = [], []
    for key, default in known.items():
        key = key[:255]
        tr_ = mem.get(default, {})
        lbl = existing.get(key)
        if lbl is None:
            new.append(
                Label(
                    key=key,
                    group=group_for(key),
                    default=default,
                    en=default,
                    ml=tr_.get("ml", ""),
                    hi=tr_.get("hi", ""),
                    ta=tr_.get("ta", ""),
                )
            )
            continue
        dirty = False
        if lbl.default != default and lbl.en == lbl.default:
            # wording changed in a new version and the customer never renamed it
            lbl.default = lbl.en = default
            for code in ("ml", "hi", "ta"):
                if tr_.get(code):
                    setattr(lbl, code, tr_[code])
            dirty = True
        if fill_missing_translations:
            for code in ("ml", "hi", "ta"):
                if not getattr(lbl, code) and tr_.get(code):
                    setattr(lbl, code, tr_[code])
                    dirty = True
        if dirty:
            changed.append(lbl)
    Label.objects.bulk_create(new, batch_size=500)
    if changed:
        now = timezone.now()
        for lbl in changed:
            lbl.updated_at = now  # bulk_update skips auto_now; other workers detect the change by it
        Label.objects.bulk_update(changed, ["default", "en", "ml", "hi", "ta", "updated_at"], batch_size=500)
    refresh(force=True)
    return len(new), len(changed)
