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
