import datetime as dt
from decimal import Decimal, InvalidOperation

from django import template
from django.utils.html import format_html

from core import dates
from core.i18n import ui

register = template.Library()

NUMERIC_TYPES = {
    "DecimalField",
    "IntegerField",
    "PositiveIntegerField",
    "FloatField",
    "BigIntegerField",
    "PositiveSmallIntegerField",
    "SmallIntegerField",
    "BigAutoField",
    "AutoField",
}


def indian_format(value, places=2):
    """12345678.5 -> '1,23,45,678.50' (Indian digit grouping)."""
    try:
        d = Decimal(value or 0)
    except (InvalidOperation, TypeError, ValueError):
        return value
    whole, _, frac = f"{abs(d):.{places}f}".partition(".")
    if len(whole) > 3:
        head, groups = whole[:-3], []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [whole[-3:]])
    out = whole + (f".{frac}" if places else "")
    return f"-{out}" if d < 0 else out


@register.filter
def inr(value, places=2):
    return indian_format(value, int(places))


@register.filter
def num(value):
    """Number without needless decimals: 12.000 -> 12, 1.500 -> 1.50."""
    if value is None or value == "":
        return ""
    try:
        d = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return value
    if d == d.to_integral():
        return indian_format(d, 0)
    return indian_format(d, 2 if abs(d) >= 1 else 3)


# payment states (payroll 'Payment' and a payment entry's status) shown as coloured chips in lists
STATE_BADGES = {
    "paid": "success",
    "received": "success",
    "part": "info",
    "unpaid": "danger",
    "carried": "secondary",
    "pending": "warning",
}


def cell_value(obj, name, plain=False):
    """Display value of one field for lists, CSV and PDF."""
    field = obj._meta.get_field(name)
    if field.is_relation:
        related = getattr(obj, name)
        return "" if related is None else str(related)
    raw = getattr(obj, name)
    if field.choices:
        text = ui(str(getattr(obj, f"get_{name}_display")()))
        colour = STATE_BADGES.get(raw) if name in ("pay_state", "status", "receipt_state") and not plain else None
        return format_html('<span class="badge rounded-pill text-bg-{}">{}</span>', colour, text) if colour else text
    if raw is None:
        return ""
    if not plain and field.get_internal_type() == "TextField" and raw:
        # long notes: one short line in lists, the whole text on hover
        return format_html('<span class="cell-note" title="{}">{}</span>', raw, raw)
    if isinstance(raw, bool):
        if plain:
            return ui("Yes") if raw else ui("No")
        return format_html(
            '<i class="bi {} text-{}" title="{}"></i>',
            "bi-check-circle-fill" if raw else "bi-x-circle",
            "success" if raw else "secondary",
            ui("Yes") if raw else ui("No"),
        )
    if isinstance(raw, dt.datetime):
        return dates.fmt(raw)
    if isinstance(raw, dt.date):
        return dates.fmt(raw)
    if isinstance(raw, Decimal):
        return str(raw) if plain == "csv" else num(raw)
    return raw


@register.simple_tag(takes_context=True)
def qs_replace(context, **kwargs):
    """The current query string with some keys replaced / removed."""
    q = context["request"].GET.copy()
    for k, v in kwargs.items():
        if v in (None, ""):
            q.pop(k, None)
        else:
            q[k] = v
    return q.urlencode()


@register.filter
def get_item(d, key):
    try:
        return d.get(key)
    except AttributeError:
        return None


@register.filter
def get_index(seq, idx):
    try:
        return seq[int(idx)]
    except (IndexError, TypeError, ValueError):
        return None


@register.filter
def split(value, sep=" "):
    return value.split(sep)


@register.filter
def field(form, name):
    return form[name]


@register.filter
def get_attr(obj, name):
    return getattr(obj, name, "")
