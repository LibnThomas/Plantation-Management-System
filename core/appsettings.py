"""The installation's App Settings row, loaded once per request, plus the theme CSS made from it."""

import contextvars

from django.db import DatabaseError

_current = contextvars.ContextVar("pms_app_settings", default=None)


def load():
    """Read the settings row (one query) and keep it for the rest of this request. The logo image is
    left out (pages only need logo_type); it is read when the logo itself is served."""
    from .models import AppSettings

    try:
        app = AppSettings.objects.select_related("font").defer("logo").get_or_create(pk=1)[0]
    except DatabaseError:  # tables not created yet (first migrate)
        app = AppSettings(pk=1)
    _current.set(app)
    return app


def current():
    return _current.get() or load()


def forget():
    _current.set(None)


# ---------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------
def _rgb(hex_color):
    h = hex_color.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


def _hex(rgb):
    return "#" + "".join(f"{max(0, min(255, round(c))):02x}" for c in rgb)


def mix(hex_color, other, amount):
    """Blend towards another colour (amount 0..1)."""
    a, b = _rgb(hex_color), _rgb(other)
    return _hex([x + (y - x) * amount for x, y in zip(a, b, strict=True)])


def luminance(hex_color):
    def channel(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in _rgb(hex_color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(c1, c2):
    l1, l2 = sorted((luminance(c1), luminance(c2)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def theme_css(app, text_size=100):
    """CSS variables for the chosen colours, font and text size (inserted in every page)."""
    p, head, bg = app.primary_color, app.header_color, app.background_color
    r, g, b = _rgb(p)
    font = app.font.css_stack if app.font_id and app.font else 'system-ui, -apple-system, "Segoe UI", Roboto, Arial'
    font = "".join(ch for ch in font if ch not in "<>{};")  # stays inside the CSS value
    size = app.base_font_size * text_size / 100
    return (
        ":root{"
        f"--pms-green:{p};--pms-green-2:{mix(p, '#ffffff', 0.15)};--pms-green-dark:{mix(p, '#000000', 0.2)};"
        f"--pms-leaf:{mix(p, '#ffffff', 0.9)};--pms-bg:{bg};--pms-head:{head};"
        f"--bs-primary:{p};--bs-primary-rgb:{r},{g},{b};--bs-link-color:{p};--bs-link-color-rgb:{r},{g},{b};"
        f'--pms-font:{font},"PMS Malayalam","PMS Tamil","PMS Devanagari",sans-serif;'
        f"--pms-size:{size:.1f}px}}"
    )
