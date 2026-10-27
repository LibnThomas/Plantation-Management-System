from django import template

from core import i18n

register = template.Library()


@register.simple_tag
def t(text, **fmt):
    """{% t "Save" %} or {% t "{n} records" n=count %} - translated / renamed UI text."""
    return i18n.ui(text, **fmt)


register.simple_tag(i18n.module_title, name="module_title")


@register.simple_tag
def module_help(cfg):
    return i18n.tr(f"module_help:{cfg.slug}", cfg.help_text) if cfg.help_text else ""


@register.filter
def tr_ui(text):
    """A text that comes from the code (e.g. a choice label), translated: {{ value|tr_ui }}."""
    return i18n.ui(str(text))


@register.simple_tag
def static_v(path):
    """URL of one of our own static files with ?v=<app version>.<file time>: a changed file is always
    fetched again (never an old copy from the browser / installed-app cache), even before the app version
    is raised or the server restarted."""
    import os

    from django.conf import settings
    from django.contrib.staticfiles import finders
    from django.templatetags.static import static

    found = finders.find(path)
    try:
        stamp = int(os.path.getmtime(found)) if found else 0
    except OSError:
        stamp = 0
    return f"{static(path)}?v={settings.PWA_VERSION}.{stamp}"
