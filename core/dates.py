"""
One date format everywhere: DD/MM/YY (e.g. 13/10/26) - on screens, in date boxes, PDFs and CSV files.

Typing is forgiving: 13/10/26, 13/10/2026, 13-10-26, 13.10.2026 and 2026-10-13 are all understood.
A two-digit year is taken as the nearest sensible century: up to 20 years ahead, otherwise the past
(so a birth date typed as 15/06/65 is 1965, not 2065).
"""

import datetime as dt
import re

from django.utils import timezone

DISPLAY = "%d/%m/%y"
DISPLAY_TIME = "%d/%m/%y %H:%M"
PLACEHOLDER = "DD/MM/YY"
DJANGO_FORMAT = "d/m/y"  # the same, for the template |date filter

_DMY = re.compile(r"^\s*(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2}|\d{4})\s*$")
_ISO = re.compile(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})\s*$")


def full_year(yy, today=None):
    year = 2000 + yy
    this_year = (today or timezone.localdate()).year
    return year - 100 if year > this_year + 20 else year


def parse(value):
    """A date from text in any of the accepted forms, or None."""
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    text = str(value or "")
    m = _ISO.match(text)
    if m:
        y, mo, d = (int(x) for x in m.groups())
    else:
        m = _DMY.match(text)
        if not m:
            return None
        d, mo, y = (int(x) for x in m.groups())
        if len(m.group(3)) == 2:
            y = full_year(y)
    try:
        day = dt.date(y, mo, d)
    except ValueError:
        return None
    return day if 1900 <= day.year <= 2200 else None  # far-off dates would overflow date maths


def fmt(value):
    """13/10/26 (date or date-time); empty for None."""
    if not value:
        return ""
    if isinstance(value, dt.datetime):
        return (
            timezone.localtime(value).strftime(DISPLAY_TIME)
            if timezone.is_aware(value)
            else value.strftime(DISPLAY_TIME)
        )
    return value.strftime(DISPLAY)
