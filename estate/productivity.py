"""
Harvest per person, worked out by the app (nothing extra to enter).

For a harvest the people of the same date who did its picking / harvesting job are fetched, each source
kept apart:
  - Work Details - Individual: employees with a work entry for that job. Attendance decides how much of
    a day they count: Full day = 1, Half day = 0.5, Absent / leave = not counted; no attendance marked =
    full day. Their OT hours come from the work entries (or from attendance when the entries have none).
  - Daily Labour Count: total no. of workers (13.5 allowed) and OT hours (morning + evening).

Everything is counted in hours (App Settings: hours in a full day, usually 8):
    total hours   = people x 8 + OT hours                 10 people, 6 OT hours -> 80 + 6 = 86 h
    kg per hour   = day's quantity / total hours          200 kg / 86 h = 2.33 kg
    full day, 1 person            = kg per hour x 8                         = 18.60 kg
    OT, 1 OT person               = kg per hour x average OT hours of the people who did OT
                                    (6 h by 3 people = 2 h)                  = 4.65 kg
    full day + OT, 1 OT person    = kg per hour x (8 + 2)                   = 23.26 kg
    average, 1 person             = day's quantity / people (10)            = 20.00 kg
    harvest in full days (all)    = kg per hour x people x 8                = 186.05 kg
    harvest in OT (all)           = kg per hour x OT hours                  = 13.95 kg
All harvests of the same date and job are added together. The figures are stored on the harvest and
refreshed whenever a harvest, attendance, work entry or labour count of that date is saved or deleted.
"""

import contextvars
from collections import namedtuple
from contextlib import contextmanager
from decimal import Decimal

from django.db import DatabaseError
from django.db.models import Sum

D0 = Decimal(0)
Q3 = Decimal("0.001")
Models = namedtuple("Models", "Harvest GroupWorkEntry WorkEntry Attendance AppSettings")

FIELDS = (
    "ind_people",
    "ind_ot_people",
    "ind_ot_hours",
    "count_people",
    "count_ot_people",
    "count_ot_hours",
    "total_hours",
    "per_hour",
    "full_day_quantity",
    "ot_quantity",
    "per_person_full_day",
    "per_person_ot",
    "per_person_full_day_ot",
    "per_person_average",
)
EMPTY = dict.fromkeys(FIELDS, D0)


def _models():
    from core.models import AppSettings

    from . import models as m

    return Models(m.Harvest, m.GroupWorkEntry, m.WorkEntry, m.Attendance, AppSettings)


def day_hours(M=None, using="default"):
    M = M or _models()
    # App Settings are shared by the real and the demo data
    try:
        hours = M.AppSettings.objects.using("default").filter(pk=1).values_list("work_hours_per_day", flat=True).first()
    except DatabaseError:  # settings table not up to date yet (during migrate)
        hours = None
    return Decimal(hours or 8)


def _q(value):
    return Decimal(value).quantize(Q3)


def people(day, job_id, using="default", M=None):
    """The people and OT hours of one date and job, each source apart (see the module text)."""
    M = M or _models()
    rng = {"date": day, "job_type_id": job_id}

    # Work Details - Individual (each employee once, weighted by attendance)
    entries = {}
    for emp_id, ot in M.WorkEntry.objects.using(using).filter(**rng).values_list("employee_id", "ot_hours"):
        entries[emp_id] = entries.get(emp_id, D0) + Decimal(ot or 0)
    attendance = {
        e: (Decimal(fraction or 0), Decimal(ot or 0))
        for e, fraction, ot in M.Attendance.objects.using(using)
        .filter(date=day, employee_id__in=list(entries))
        .values_list("employee_id", "status__pay_fraction", "ot_hours")
    }
    ind_people = ind_ot_people = ind_ot_hours = D0
    for emp_id, entry_ot in entries.items():
        fraction, att_ot = attendance.get(emp_id, (Decimal(1), D0))  # no attendance marked: full day
        if not fraction:  # absent / on leave
            continue
        ot = entry_ot or att_ot
        ind_people += fraction
        ind_ot_hours += ot
        ind_ot_people += 1 if ot else 0

    # Daily Labour Count
    g = (
        M.GroupWorkEntry.objects.using(using)
        .filter(**rng)
        .aggregate(
            w=Sum("workers_count"), m=Sum("ot_morning_workers"), e=Sum("ot_evening_workers"), h=Sum("ot_total_hours")
        )
    )
    return {
        "ind_people": ind_people,
        "ind_ot_people": ind_ot_people,
        "ind_ot_hours": ind_ot_hours,
        "count_people": Decimal(g["w"] or 0),
        "count_ot_people": Decimal(g["m"] or 0) + Decimal(g["e"] or 0),
        "count_ot_hours": Decimal(g["h"] or 0),
    }


def calculate(p, quantity, hours):
    """From the people (see people()) and the day's quantity: hours worked and harvest per hour / person.
    The form's live preview (templates/estate/form.html) does the same sums in the browser."""
    quantity = Decimal(quantity or 0)
    heads, ot_hours = p["ind_people"] + p["count_people"], p["ind_ot_hours"] + p["count_ot_hours"]
    ot_people = p["ind_ot_people"] + p["count_ot_people"]
    total_hours = heads * hours + ot_hours
    per_hour = quantity / total_hours if total_hours else D0
    avg_ot = ot_hours / ot_people if ot_people else D0
    return {
        **p,
        "total_hours": total_hours,
        "per_hour": _q(per_hour),
        "full_day_quantity": _q(per_hour * heads * hours),
        "ot_quantity": _q(per_hour * ot_hours),
        "per_person_full_day": _q(per_hour * hours),
        "per_person_ot": _q(per_hour * avg_ot),
        "per_person_full_day_ot": _q(per_hour * (hours + avg_ot)),
        "per_person_average": _q(quantity / heads) if heads else D0,
    }


def figures(day, job_id, using="default", M=None, hours=None):
    """Everything stored on the harvests of one date and job."""
    M = M or _models()
    quantity = M.Harvest.objects.using(using).filter(date=day, job_type_id=job_id).aggregate(q=Sum("quantity"))["q"]
    return _stored(M, calculate(people(day, job_id, using, M), quantity, hours or day_hours(M)))


def _stored(M, values):
    """Only the fields the Harvest table has (migrations run this with older versions of the model)."""
    names = {f.name for f in M.Harvest._meta.get_fields()}
    return {k: v for k, v in values.items() if k in names}


def refresh(day, job_id=None, using="default", M=None):
    """Re-work the figures of the harvests on a date (one job, or every job when job_id is None)."""
    if not day:
        return
    if _paused.get() is not None:
        _paused.get().add((day, job_id, using))
        return
    M = M or _models()
    harvests = M.Harvest.objects.using(using).filter(date=day, job_type__isnull=False)
    if job_id:
        harvests = harvests.filter(job_type_id=job_id)
    jobs = set(harvests.values_list("job_type_id", flat=True))
    hours = day_hours(M) if jobs else None
    for job in jobs:
        M.Harvest.objects.using(using).filter(date=day, job_type_id=job).update(**figures(day, job, using, M, hours))


def refresh_all(using="default", M=None):
    M = M or _models()
    M.Harvest.objects.using(using).filter(job_type__isnull=True).update(**_stored(M, EMPTY))
    hours = day_hours(M)
    for day, job in (
        M.Harvest.objects.using(using).filter(job_type__isnull=False).values_list("date", "job_type_id").distinct()
    ):
        M.Harvest.objects.using(using).filter(date=day, job_type_id=job).update(**figures(day, job, using, M, hours))


_paused = contextvars.ContextVar("pms_harvest_refresh_paused", default=None)


@contextmanager
def paused():
    """Bulk saving (daily attendance, demo data): the harvests touched are refreshed once at the end."""
    if _paused.get() is not None:  # already inside a pause
        yield
        return
    pending = set()
    token = _paused.set(pending)
    try:
        yield
    finally:
        _paused.reset(token)
    for using in {u for _, _, u in pending}:
        keys = [(day, job) for day, job, u in pending if u == using]
        if len(keys) > 50:  # a big batch: simply work out every harvest again
            refresh_all(using)
            continue
        whole_days = {day for day, job in keys if job is None}
        for day in whole_days:
            refresh(day, None, using)
        for day, job in keys:
            if job is not None and day not in whole_days:
                refresh(day, job, using)


class RefreshHarvests:
    """Mixin for rows that change the figures: harvests, labour counts, work entries, attendance."""

    refresh_whole_day = False  # attendance: affects every job of the date

    def _key(self):
        return (self.date, None if self.refresh_whole_day else self.job_type_id)

    def _stored_key(self):
        if not self.pk:
            return None
        field = "date" if self.refresh_whole_day else "job_type_id"
        row = type(self).objects.using(self._state.db or "default").filter(pk=self.pk).values_list("date", field)
        row = row.first()
        return (row[0], None if self.refresh_whole_day else row[1]) if row else None

    def save(self, *args, **kwargs):
        before = self._stored_key()
        super().save(*args, **kwargs)
        using = self._state.db or "default"
        refresh(*self._key(), using)
        if before and before != self._key():
            refresh(*before, using)

    def delete(self, *args, **kwargs):
        using, key = self._state.db or "default", self._key()
        result = super().delete(*args, **kwargs)
        refresh(*key, using)
        return result
