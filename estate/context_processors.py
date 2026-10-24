from django.conf import settings

from core import appsettings
from core.i18n import LANGUAGES, N_, current_lang, tr, ui

from .registry import MENU_GROUPS, MODULES


class _TextSizes:
    """Per-user text size choices (percent of the estate's text size), labels in the user's language."""

    VALUES = ((90, N_("Small")), (100, N_("Normal")), (115, N_("Large")), (130, N_("Extra large")))

    def __iter__(self):
        return iter([(v, ui(label)) for v, label in self.VALUES])


TEXT_SIZES = _TextSizes()


def table_tools_text():
    """Texts of the in-browser table search (static/js/table-tools.js), in the user's language."""
    return {
        "search": ui("Search these rows…"),
        "columns": ui("Column filters"),
        "shown": ui("{n} of {total} rows"),
        "subtotal": ui("Shown rows"),
        "clear": ui("Clear"),
        "none": ui("No rows match."),
        "sort": ui("Click to sort"),
        "hideCols": ui("Columns"),
        "showAll": ui("Show all columns"),
        "hiddenN": ui("{n} hidden"),
    }


def mobile_first_login():
    """Phone app: the starting login, shown on the login page until it has been used once."""
    from django.contrib.auth import get_user_model

    if get_user_model().objects.filter(username=settings.MOBILE_DEFAULT_USER, last_login__isnull=True).exists():
        return {"user": settings.MOBILE_DEFAULT_USER, "password": settings.MOBILE_DEFAULT_PASSWORD}
    return None


def app_context(request):
    app = getattr(request, "app_settings", None) or appsettings.current()
    menu = []
    for key, label in MENU_GROUPS:
        items = [{"cfg": c, "title": tr(f"module:{c.slug}", c.title)} for c in MODULES if c.group == key and c.in_menu]
        menu.append({"key": key, "label": tr(f"menu:{key}", label), "items": items})
    return {
        "APP": app,
        "ESTATE_NAME": app.estate_name,
        "CURRENCY": app.currency_symbol,
        "THEME_CSS": appsettings.theme_css(app, getattr(request, "text_size", 100)),
        "LICENCE": getattr(request, "licence", None),
        "TEXT_SIZES": TEXT_SIZES,
        "TEXT_SIZE": getattr(request, "text_size", 100),
        "TABLE_TOOLS_TEXT": table_tools_text,
        "ASSET_V": settings.PWA_VERSION,  # ?v= on our own styles / scripts: a new version is never served from cache
        "POPUP": request.GET.get("popup") == "1",  # a form opened from an "Add new" drop-down entry
        "MENU": menu,
        "LANGUAGES_UI": LANGUAGES,
        "CURRENT_LANG": current_lang(),
        "DEMO_MODE": getattr(request, "demo_mode", False),
        "MOBILE_APP": settings.MOBILE_APP,
        "MOBILE_FIRST_LOGIN": mobile_first_login if settings.MOBILE_APP else None,  # only read on the login page
    }
