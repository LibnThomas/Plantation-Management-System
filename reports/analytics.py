"""
Pre-built analysis for the Dashboard and the Analysis page.

Time series are bucketed in Python so the financial year and the crop season (start months in App Settings)
work next to day / week / month / quarter / year. Names of crops, job types,
categories ... come from the database in the user's language.
"""

import contextvars
import datetime as dt
from collections import OrderedDict, defaultdict
from contextlib import contextmanager
from decimal import Decimal

from django.db.models import Count, Max, Sum
from django.utils.dateformat import format as date_format

from core import appsettings, dates
from core.i18n import ui
from estate import models as m

D0 = Decimal(0)


def fy_month():
    """First month of the financial year (App Settings)."""
    return appsettings.current().financial_year_start_month


def season_month():
    """First month of the crop season (App Settings)."""
    return appsettings.current().crop_season_start_month


def year_of(d, start_month):
    """The year a financial year / season that contains `d` started in."""
    return d.year if d.month >= start_month else d.year - 1


def year_span(d, start_month, offset=0):
    """(first day, last day) of the financial year / season containing `d` (offset -1 = the one before)."""
    y = year_of(d, start_month) + offset
    start = dt.date(y, start_month, 1)
    return start, dt.date(y + 1, start_month, 1) - dt.timedelta(days=1)


def _span(start_month):
    """'Apr-Mar' in the user's language."""
    first = dt.date(2000, start_month, 1)
    last = dt.date(2000, (start_month - 2) % 12 + 1, 1)
    return f"{date_format(first, 'M')}-{date_format(last, 'M')}"


PERIODS = ["day", "week", "month", "quarter", "year", "fy", "season"]


def period_choices():
    return [
        ("day", ui("Day")),
        ("week", ui("Week")),
        ("month", ui("Month")),
        ("quarter", ui("Quarter")),
        ("year", ui("Calendar year")),
        ("fy", ui("Financial year ({months})", months=_span(fy_month()))),
        ("season", ui("Crop season ({months})", months=_span(season_month()))),
    ]


def bucket_key(d, period):
    if period == "day":
        return d.isoformat(), dates.fmt(d)
    if period == "week":
        y, w, _ = d.isocalendar()
        return f"{y}-{w:02d}", f"{ui('Wk')} {w} ({dates.fmt(d - dt.timedelta(days=d.weekday()))})"
    if period == "month":
        return f"{d.year}-{d.month:02d}", date_format(d, "M Y")
    if period == "quarter":
        q = (d.month - 1) // 3 + 1
        return f"{d.year}-Q{q}", f"Q{q} {d.year}"
    if period == "fy":
        y = year_of(d, fy_month())
        return f"{y}", f"{ui('FY')} {y}-{str(y + 1)[-2:]}"
    if period == "season":
        y = year_of(d, season_month())
        return f"{y}", f"{ui('Season')} {y}-{str(y + 1)[-2:]}"
    return f"{d.year}", str(d.year)


def all_buckets(d1, d2, period):
    """Every bucket between two dates, so charts show empty periods too."""
    out, d = OrderedDict(), d1
    while d <= d2 and len(out) < 4000:
        k, label = bucket_key(d, period)
        out.setdefault(k, label)
        d += dt.timedelta(days=1)
    return out


def series_by_period(rows, d1, d2, period, series_key=None):
    """rows: dicts with date, v and (optional) a series name. Returns chart + table data."""
    buckets = all_buckets(d1, d2, period)
    data = defaultdict(lambda: defaultdict(lambda: D0))
    names = OrderedDict()
    not_set = ui("(not set)")
    for r in rows:
        if r["date"] is None:
            continue
        k, _ = bucket_key(r["date"], period)
        s = (r.get(series_key) if series_key else ui("Total")) or not_set
        names[s] = True
        data[s][k] += Decimal(r["v"] or 0)
    keys = list(buckets)
    table = []
    for k in keys:
        vals = [data[s][k] for s in names]
        table.append([buckets[k], *vals, sum(vals, D0)])
    totals = [sum((data[s][k] for k in keys), D0) for s in names]
    return {
        "labels": [buckets[k] for k in keys],
        "names": [str(s) for s in names],
        "series": [{"label": str(s), "data": [float(data[s][k]) for k in keys]} for s in names],
        "table": table,
        "totals": totals + [sum(totals, D0)],
    }


_names_cache = contextvars.ContextVar("pms_names_cache", default=None)


@contextmanager
def shared_names():
    """Within one page (also usable as a decorator) every master list is read only once."""
    token = _names_cache.set({})
    try:
        yield
    finally:
        _names_cache.reset(token)


def _names(model):
    cache = _names_cache.get()
    if cache is None:
        return {o.pk: str(o) for o in model.objects.all()}
    if model not in cache:
        cache[model] = {o.pk: str(o) for o in model.objects.all()}
    return cache[model]


def _by_fk(qs, fk, value_expr):
    """[(fk id, value)] aggregated."""
    return list(qs.order_by().values_list(fk).annotate(v=value_expr))


def labour_cost_by_job(d1, d2):
    rng = {"date__range": (d1, d2)}
    jobs, ops = _names(m.JobType), _names(m.OperationType)
    totals = defaultdict(lambda: D0)
    for jid, v in _by_fk(m.WorkEntry.objects.filter(**rng), "job_type", Sum("total_amount")):
        totals[jobs.get(jid, "?")] += v or D0
    for jid, v in _by_fk(m.GroupWorkEntry.objects.filter(**rng), "job_type", Sum("total_cost")):
        totals[jobs.get(jid, "?")] += v or D0
    for oid, v in _by_fk(m.FieldOperation.objects.filter(**rng), "operation_type", Sum("labour_cost")):
        if v:
            totals[f"{ui('Field work')}: {ops.get(oid, '?')}"] += v
    items = sorted(((n, v) for n, v in totals.items() if v), key=lambda x: -x[1])
    total = sum((v for _, v in items), D0)
    return [{"name": n, "amount": v, "pct": v * 100 / total if total else D0} for n, v in items], total


def cost_rows(d1, d2):
    """Every cost as {date, v, cat} for time series (labour costs also carry labour=True)."""
    rng = {"date__range": (d1, d2)}
    rows = []
    cat_work, cat_group = ui("Labour - individual work"), ui("Labour - daily labour count")
    cat_input, cat_field, cat_proc = ui("Fertilizer & chemicals"), ui("Labour - field work"), ui("Processing")
    for d, v in m.WorkEntry.objects.filter(**rng).order_by().values_list("date").annotate(v=Sum("total_amount")):
        rows.append({"date": d, "v": v, "cat": cat_work, "labour": True})
    for d, v in m.GroupWorkEntry.objects.filter(**rng).order_by().values_list("date").annotate(v=Sum("total_cost")):
        rows.append({"date": d, "v": v, "cat": cat_group, "labour": True})
    for d, li, ll in (
        m.FieldOperation.objects.filter(**rng)
        .order_by()
        .values_list("date")
        .annotate(li=Sum("input_cost"), ll=Sum("labour_cost"))
    ):
        rows.append({"date": d, "v": li, "cat": cat_input})
        rows.append({"date": d, "v": ll, "cat": cat_field, "labour": True})
    for d, v in m.ProcessingBatch.objects.filter(**rng).order_by().values_list("date").annotate(v=Sum("total_cost")):
        rows.append({"date": d, "v": v, "cat": cat_proc})
    cats = _names(m.ExpenseCategory)
    for d, c, v in m.Expense.objects.filter(**rng).order_by().values_list("date", "category").annotate(v=Sum("amount")):
        rows.append({"date": d, "v": v, "cat": cats.get(c, "?")})
    return rows


def cost_summary(d1, d2, rows=None):
    by_cat = defaultdict(lambda: D0)
    for r in rows if rows is not None else cost_rows(d1, d2):
        by_cat[r["cat"]] += Decimal(r["v"] or 0)
    items = sorted(((k, v) for k, v in by_cat.items() if v), key=lambda x: -x[1])
    total = sum((v for _, v in items), D0)
    return [{"name": k, "amount": v, "pct": v * 100 / total if total else D0} for k, v in items], total


def _product_series(qs, value_field, d1, d2, period):
    names = _names(m.Product)
    rows = [
        {"date": d, "v": v, "p": names.get(p)}
        for d, p, v in qs.order_by().values_list("date", "product").annotate(v=Sum(value_field))
    ]
    return series_by_period(rows, d1, d2, period, series_key="p")


def production(d1, d2, period, product=None):
    qs = m.Harvest.objects.filter(date__range=(d1, d2))
    return _product_series(qs.filter(product=product) if product else qs, "quantity", d1, d2, period)


def processed_output(d1, d2, period, product=None):
    qs = m.ProcessingBatch.objects.filter(date__range=(d1, d2))
    return _product_series(qs.filter(product=product) if product else qs, "output_quantity", d1, d2, period)


def recovery_by_product(d1, d2):
    products, out = {p.pk: p for p in m.Product.objects.select_related("unit")}, []
    for r in (
        m.ProcessingBatch.objects.filter(date__range=(d1, d2))
        .order_by()
        .values("product")
        .annotate(i=Sum("input_quantity"), o=Sum("output_quantity"), n=Count("id"), c=Sum("total_cost"))
    ):
        i, o, c = r["i"] or D0, r["o"] or D0, r["c"] or D0
        p = products.get(r["product"])
        out.append(
            {
                "product": str(p) if p else "",
                "unit": str(p.unit) if p and p.unit_id else "",
                "input": i,
                "output": o,
                "batches": r["n"],
                "cost": c,
                "recovery": o * 100 / i if i else D0,
                "ratio": i / o if o else D0,
                "cost_per_unit": c / o if o else D0,
            }
        )
    return sorted(out, key=lambda x: x["product"])


def input_usage(d1, d2):
    inputs = {a.pk: a for a in m.AgriInput.objects.select_related("unit")}
    out = []
    for r in (
        m.FieldOperation.objects.filter(date__range=(d1, d2), agri_input__isnull=False)
        .order_by()
        .values("agri_input")
        .annotate(qty=Sum("quantity"), cost=Sum("input_cost"), n=Count("id"), last=Max("date"))
        .order_by("-cost")
    ):
        a = inputs.get(r["agri_input"])
        out.append({**r, "name": a.name if a else "?", "unit": str(a.unit) if a and a.unit_id else ""})
    return out


def last_operation_per_block():
    """Latest date of each operation in each block: 'when did we last spray block X?'"""
    ops = list(m.OperationType.objects.all())
    blocks = _names(m.Block)
    latest = defaultdict(dict)
    for b, o, last in (
        m.FieldOperation.objects.order_by().values_list("block", "operation_type").annotate(last=Max("date"))
    ):
        latest[blocks.get(b, ui("(no block)"))][o] = last
    used = [o for o in ops if any(o.pk in v for v in latest.values())]
    return {
        "ops": [(o.pk, str(o)) for o in used],
        "rows": [(b, [vals.get(o.pk) for o in used]) for b, vals in sorted(latest.items())],
    }


def block_performance(d1, d2):
    rng = {"date__range": (d1, d2)}
    products = _names(m.Product)
    harvest = defaultdict(lambda: defaultdict(lambda: D0))  # block -> product id -> quantity
    for b, p, q in (
        m.Harvest.objects.filter(**rng).order_by().values_list("block", "product").annotate(q=Sum("quantity"))
    ):
        harvest[b][p] += q or D0
    costs = defaultdict(lambda: D0)
    for qs, field in (
        (m.FieldOperation, "total_cost"),
        (m.GroupWorkEntry, "total_cost"),
        (m.WorkEntry, "total_amount"),
        (m.Expense, "amount"),
    ):
        for b, v in qs.objects.filter(**rng).order_by().values_list("block").annotate(v=Sum(field)):
            costs[b] += v or D0
    out = []
    for b in m.Block.objects.select_related("main_crop"):
        crops = harvest[b.pk]
        # yield is only meaningful for one crop: the block's main crop (else everything, e.g. one-crop estates)
        qty = crops.get(b.main_crop_id, D0) if b.main_crop_id else sum(crops.values(), D0)
        area = b.area_acres or D0
        out.append(
            {
                "block": b,
                "harvest": {products.get(p): q for p, q in crops.items()},
                "total_qty": qty,
                "cost": costs[b.pk],
                "yield_per_acre": qty / area if area else None,
                "cost_per_acre": costs[b.pk] / area if area else None,
            }
        )
    return out


def sales_summary(d1, d2):
    names = _names(m.Product)
    rows = (
        m.Sale.objects.filter(date__range=(d1, d2))
        .order_by()
        .values("product")
        .annotate(qty=Sum("quantity"), amount=Sum("total_amount"), n=Count("id"))
        .order_by("-amount")
    )
    return [
        {**r, "name": names.get(r["product"]), "avg_rate": r["amount"] / r["qty"] if r["qty"] else D0} for r in rows
    ]


def workforce_mandays(d1, d2, period):
    s_att, s_group = ui("Attendance"), ui("Daily labour count")
    rows = [
        {"date": d, "v": v, "s": s_att}
        for d, v in m.Attendance.objects.filter(date__range=(d1, d2))
        .order_by()
        .values_list("date")
        .annotate(v=Sum("status__pay_fraction"))
    ]
    rows += [
        {"date": d, "v": n, "s": s_group}
        for d, n in m.GroupWorkEntry.objects.filter(date__range=(d1, d2))
        .order_by()
        .values_list("date")
        .annotate(n=Sum("workers_count"))
    ]
    return series_by_period(rows, d1, d2, period, series_key="s")


def employee_summary(d1, d2):
    """Per employee: days in each attendance status, man-days (paid part of days) and OT hours."""
    statuses = list(m.AttendanceStatus.objects.all())
    fraction = {s.pk: s.pay_fraction or D0 for s in statuses}
    counts, mandays, ot = defaultdict(dict), defaultdict(lambda: D0), defaultdict(lambda: D0)
    for emp, st, n, hours in (
        m.Attendance.objects.filter(date__range=(d1, d2))
        .order_by()
        .values_list("employee", "status")
        .annotate(n=Count("id"), ot=Sum("ot_hours"))
    ):
        counts[emp][st] = n
        mandays[emp] += n * fraction.get(st, D0)
        ot[emp] += hours or D0
    out = []
    for e in m.Employee.objects.filter(pk__in=counts).select_related("job_type").order_by("full_name"):
        out.append(
            {
                "emp": e,
                "counts": [counts[e.pk].get(s.pk, 0) for s in statuses],
                "mandays": mandays[e.pk],
                "ot": ot[e.pk],
            }
        )
    return statuses, out
