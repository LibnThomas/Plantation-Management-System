"""
Report Builder engine.

Every reportable model exposes its fields plus the fields of related records (up to two levels,
e.g. Attendance › Employee › Job type), so the user can pick any columns, filter on any field,
group by any field or date period and total numeric fields. Labels follow the user's language
and the customer's own renames.
"""

import datetime as dt
from collections import OrderedDict
from decimal import Decimal, InvalidOperation

from django.db.models import Avg, Count, Max, Min, Q, Sum
from django.db.models.functions import TruncDay, TruncMonth, TruncQuarter, TruncWeek, TruncYear
from django.utils.dateformat import format as date_format

from core import dates
from core.i18n import field_label, tr, ui
from estate.models import LocalizedNameMixin, Lookup
from estate.registry import MODULES
from estate.templatetags.estate_tags import NUMERIC_TYPES

SKIP = {"id", "created_by", "created_at", "updated_at", "name_ml", "name_hi", "name_ta", "breakdown", "params"}
LOOKUP_SKIP = {"sort_order", "is_active", "color"}

TRUNC = OrderedDict(
    [("day", TruncDay), ("week", TruncWeek), ("month", TruncMonth), ("quarter", TruncQuarter), ("year", TruncYear)]
)
AGG_FUNCS = OrderedDict([("sum", Sum), ("avg", Avg), ("min", Min), ("max", Max), ("count", Count)])


def period_names():
    return OrderedDict(
        [
            ("day", ui("Day")),
            ("week", ui("Week")),
            ("month", ui("Month")),
            ("quarter", ui("Quarter")),
            ("year", ui("Year")),
        ]
    )


def agg_names():
    return OrderedDict(
        [("sum", ui("Sum")), ("avg", ui("Average")), ("min", ui("Min")), ("max", ui("Max")), ("count", ui("Count"))]
    )


OPERATOR_CODES = {
    "text": {"contains", "eq", "ne", "startswith", "in", "isnull", "notnull"},
    "number": {"eq", "ne", "gt", "gte", "lt", "lte", "between", "in", "isnull", "notnull"},
    "date": {"eq", "gte", "lte", "between", "gt", "lt", "in", "isnull", "notnull"},
    "bool": {"true", "false"},
}


def operators():
    return {
        "text": [
            ("contains", ui("contains")),
            ("eq", ui("is")),
            ("ne", ui("is not")),
            ("startswith", ui("starts with")),
            ("in", ui("is one of (comma separated)")),
            ("isnull", ui("is empty")),
            ("notnull", ui("is not empty")),
        ],
        "number": [
            ("eq", "="),
            ("ne", "≠"),
            ("gt", ">"),
            ("gte", "≥"),
            ("lt", "<"),
            ("lte", "≤"),
            ("between", ui("between")),
            ("isnull", ui("is empty")),
            ("notnull", ui("is not empty")),
        ],
        "date": [
            ("eq", ui("on")),
            ("gte", ui("on or after")),
            ("lte", ui("on or before")),
            ("between", ui("between")),
            ("gt", ui("after")),
            ("lt", ui("before")),
            ("isnull", ui("is empty")),
            ("notnull", ui("is not empty")),
        ],
        "bool": [("true", ui("is Yes")), ("false", ui("is No"))],
    }


def _ftype(field):
    t = field.get_internal_type()
    if t in NUMERIC_TYPES:
        return "number"
    if t in ("DateField", "DateTimeField"):
        return "date"
    if t == "BooleanField":
        return "bool"
    return "text"


def _collect_fields(model, prefix="", label_prefix="", depth=0, seen=None):
    seen = seen or {model}
    is_lookup = issubclass(model, Lookup)
    out = []
    for f in model._meta.concrete_fields:
        if f.name in SKIP or (is_lookup and f.name in LOOKUP_SKIP) or f.get_internal_type() == "JSONField":
            continue
        path = f"{prefix}{f.name}"
        label = f"{label_prefix}{field_label(model, f.name)}"
        if f.is_relation:
            rel = f.related_model
            if depth < 2 and rel not in seen:
                out.extend(_collect_fields(rel, f"{path}__", f"{label} › ", depth + 1, seen | {rel}))
            continue
        localized = f.name == "name" and issubclass(model, LocalizedNameMixin)
        out.append(
            {
                "path": path,
                "label": label,
                "type": _ftype(f),
                "is_fk_child": depth > 0,
                "model": model if localized else None,
            }
        )
    return out


class Dataset:
    def __init__(self, cfg):
        self.cfg = cfg
        self.slug = cfg.slug
        self.model = cfg.model

    @property
    def title(self):
        return tr(f"module:{self.slug}", self.cfg.title)

    @property
    def fields(self):
        # rebuilt on each use: labels follow the user's language and the customer's renames
        return _collect_fields(self.model)

    @property
    def by_path(self):
        return {f["path"]: f for f in self.fields}

    @property
    def default_columns(self):
        by_path, out = self.by_path, []
        for name in self.cfg.list_fields:
            if self.model._meta.get_field(name).is_relation:
                for cand in ("full_name", "name"):
                    if f"{name}__{cand}" in by_path:
                        out.append(f"{name}__{cand}")
                        break
            elif name in by_path:
                out.append(name)
        return out

    def json_fields(self):
        return [{k: v for k, v in f.items() if k != "model"} for f in self.fields]


DATASETS = OrderedDict((cfg.slug, Dataset(cfg)) for cfg in MODULES if cfg.reportable)


# ---------------------------------------------------------------------------
# Query execution
# ---------------------------------------------------------------------------
class FilterError(ValueError):
    pass


def _coerce(ftype, value):
    value = (value or "").strip()
    if ftype == "number":
        try:
            number = Decimal(value.replace(",", ""))
        except InvalidOperation:
            number = None
        if number is None or not number.is_finite():
            raise FilterError(ui("'{value}' is not a number", value=value))
        return number
    if ftype == "date":
        try:
            day = dates.parse(value)
            if day is None:
                raise ValueError
            return day
        except ValueError:
            raise FilterError(ui("'{value}' is not a valid date", value=value)) from None
    return value


def build_q(by_path, filters):
    """filters: [{field, op, value, value2}] -> (include Q, exclude Q, error messages)."""
    inc, exc, errors = Q(), Q(), []
    for flt in filters:
        path, op = flt.get("field"), flt.get("op")
        meta = by_path.get(path)
        if not meta or not op:
            continue
        ftype = meta["type"]
        if op not in OPERATOR_CODES[ftype]:
            continue
        try:
            if op in ("isnull", "notnull"):
                cond = Q(**{f"{path}__isnull": True})
                if ftype == "text":
                    cond |= Q(**{path: ""})
                if op == "isnull":
                    inc &= cond
                else:
                    exc |= cond
                continue
            if op in ("true", "false"):
                inc &= Q(**{path: op == "true"})
                continue
            if op == "in":
                values = [_coerce(ftype, x) for x in (flt.get("value") or "").split(",") if x.strip()]
                if values:
                    inc &= Q(**{f"{path}__in": values})
                continue
            v = _coerce(ftype, flt.get("value"))
            if op == "ne":
                exc |= Q(**{path: v})
            elif op == "eq":
                inc &= Q(**{f"{path}__iexact" if ftype == "text" else path: v})
            elif op == "contains":
                inc &= Q(**{f"{path}__icontains": v})
            elif op == "startswith":
                inc &= Q(**{f"{path}__istartswith": v})
            elif op in ("gt", "gte", "lt", "lte"):
                inc &= Q(**{f"{path}__{op}": v})
            elif op == "between":
                inc &= Q(**{f"{path}__gte": v, f"{path}__lte": _coerce(ftype, flt.get("value2") or flt.get("value"))})
        except FilterError as e:
            errors.append(f"{meta['label']}: {e}")
    return inc, exc, errors


def period_label(value, period):
    if value is None:
        return ui("(blank)")
    if isinstance(value, dt.datetime):
        value = value.date()
    if period == "year":
        return str(value.year)
    if period == "quarter":
        return f"{value.year} Q{(value.month - 1) // 3 + 1}"
    if period == "month":
        return date_format(value, "Y-m (M)")
    if period == "week":
        y, w, _ = value.isocalendar()
        return f"{y}-W{w:02d} ({dates.fmt(value)})"
    return dates.fmt(value)


class _Display:
    """Turns raw values into display text (dates, Yes/No, translated list names)."""

    def __init__(self):
        self._names = {}

    def localized(self, model, value):
        if model not in self._names:
            self._names[model] = {o.name: str(o) for o in model.objects.all()}
        return self._names[model].get(value, value)

    def __call__(self, meta, value, period=None):
        if value is None or value == "":
            return ui("(blank)") if period else ""
        if period and meta["type"] == "date":
            return period_label(value, period)
        if meta.get("model"):
            return self.localized(meta["model"], value)
        if isinstance(value, bool):
            return ui("Yes") if value else ui("No")
        if isinstance(value, dt.date):
            return dates.fmt(value)
        return value


def _round(v):
    return Decimal(v).quantize(Decimal("0.01")) if isinstance(v, (Decimal, float)) else v


def run_report(ds, params, limit=None):
    """Runs the report. Returns headers, rows (display values), numeric flags, totals, chart / pivot data."""
    by_path = ds.by_path
    display = _Display()
    inc, exc, errors = build_q(by_path, params.get("filters", []))
    qs = ds.model.objects.filter(inc)
    if exc:
        qs = qs.exclude(exc)
    group = [g for g in params.get("group", []) if g in by_path][:2]
    period = params.get("period") if params.get("period") in TRUNC else "month"
    result = {"errors": errors, "grouped": bool(group), "pivot": None, "chart": None}

    if not group:
        cols = [c for c in params.get("cols", []) if c in by_path] or ds.default_columns
        sort = params.get("sort") or ""
        if sort.lstrip("-") in by_path:
            qs = qs.order_by(sort)
        metas = [by_path[c] for c in cols]
        num_cols = {i: c for i, c in enumerate(cols) if by_path[c]["type"] == "number"}
        agg = qs.aggregate(**{f"t{i}": Sum(c) for i, c in num_cols.items()}) if num_cols else {}
        values = qs.values_list(*cols)
        rows = [[display(mt, v) for mt, v in zip(metas, r, strict=True)] for r in (values[:limit] if limit else values)]
        result.update(
            headers=[mt["label"] for mt in metas],
            numeric=[mt["type"] == "number" for mt in metas],
            rows=rows,
            totals=[agg.get(f"t{i}") if i in num_cols else None for i in range(len(cols))],
            total_count=qs.count() if limit else len(rows),
            shown=len(rows),
        )
        return result

    # ---------- grouped / aggregated ----------
    names = agg_names()
    aggs = params.get("aggs") or ["count"]
    annotations, agg_headers, agg_funcs = OrderedDict(), [], []
    for i, a in enumerate(aggs):
        func, _, path = a.partition(":")
        if func == "count":
            annotations[f"a{i}"] = Count("pk")
            agg_headers.append(ui("No. of records"))
        elif func in AGG_FUNCS and by_path.get(path, {}).get("type") == "number":
            annotations[f"a{i}"] = AGG_FUNCS[func](path)
            agg_headers.append(f"{names[func]}: {by_path[path]['label']}")
        else:
            continue
        agg_funcs.append(func)
    if not annotations:
        annotations["a0"] = Count("pk")
        agg_headers, agg_funcs = [ui("No. of records")], ["count"]

    group_keys, key_metas = [], []
    for i, g in enumerate(group):
        meta = by_path[g]
        key = g
        if meta["type"] == "date":
            key = f"g{i}"
            qs = qs.annotate(**{key: TRUNC[period](g)})
        group_keys.append(key)
        key_metas.append(meta)

    data = qs.values(*group_keys).annotate(**annotations)
    first = next(iter(annotations))
    sort = params.get("sort") or ""
    data = list(
        data.order_by(f"-{first}" if sort == "-agg" else first if sort == "agg" else group_keys[0], *group_keys)
    )

    pnames = period_names()
    group_headers = [mt["label"] + (f" ({pnames[period]})" if mt["type"] == "date" else "") for mt in key_metas]
    rows, chart_labels = [], []
    for r in data:
        gvals = [
            display(mt, r[k], period if mt["type"] == "date" else None)
            for mt, k in zip(key_metas, group_keys, strict=True)
        ]
        rows.append(gvals + [_round(r[k]) for k in annotations])
        chart_labels.append(" / ".join(str(x) for x in gvals))
    n = len(group_keys)
    totals = [None] * n + [
        sum((row[n + i] or 0) for row in rows) if f in ("sum", "count") else None for i, f in enumerate(agg_funcs)
    ]
    result.update(
        headers=group_headers + agg_headers,
        numeric=[False] * n + [True] * len(annotations),
        rows=rows,
        totals=totals,
        total_count=len(rows),
        shown=len(rows),
    )
    result["chart"] = {
        "labels": chart_labels,
        "series": [{"label": h, "data": [float(row[n + i] or 0) for row in rows]} for i, h in enumerate(agg_headers)],
    }

    if params.get("pivot") and n == 2:
        row_keys, col_keys, cells = OrderedDict(), OrderedDict(), {}
        for row in rows:
            row_keys[row[0]] = col_keys[row[1]] = True
            key = (row[0], row[1])
            if key in cells and agg_funcs[0] in ("sum", "count"):
                # two raw values with the same display text (e.g. empty and blank): add them up
                cells[key] = (cells[key] or 0) + (row[2] or 0)
            else:
                cells[key] = row[2]
        cols = list(col_keys)
        prow = []
        for rk in row_keys:
            vals = [cells.get((rk, ck)) for ck in cols]
            prow.append([rk, *vals, sum(v or 0 for v in vals)])
        col_totals = [sum((cells.get((rk, ck)) or 0) for rk in row_keys) for ck in cols]
        result["pivot"] = {
            "corner": f"{group_headers[0]} ↓ / {group_headers[1]} →",
            "measure": agg_headers[0],
            "cols": cols,
            "rows": prow,
            "col_totals": col_totals + [sum(col_totals)],
        }
        result["chart"] = {
            "labels": list(row_keys),
            "stacked": True,
            "series": [{"label": str(ck), "data": [float(cells.get((rk, ck)) or 0) for rk in row_keys]} for ck in cols],
        }
    return result


# ---------------------------------------------------------------------------
# Query string <-> params
# ---------------------------------------------------------------------------
def params_from_querydict(q):
    fields, ops, vals, vals2 = q.getlist("f_field"), q.getlist("f_op"), q.getlist("f_val"), q.getlist("f_val2")
    filters = [
        {
            "field": f,
            "op": ops[i] if i < len(ops) else "",
            "value": vals[i] if i < len(vals) else "",
            "value2": vals2[i] if i < len(vals2) else "",
        }
        for i, f in enumerate(fields)
        if f
    ]
    return {
        "cols": q.getlist("cols"),
        "filters": filters,
        "group": [g for g in q.getlist("group") if g],
        "period": q.get("period", "month"),
        "aggs": [a for a in q.getlist("aggs") if a],
        "sort": q.get("sort", ""),
        "pivot": q.get("pivot") == "1",
        "chart": q.get("chart", "bar"),
    }


# Ready-made reports (one click in the builder). Names are translated on display.
PRESETS = [
    (
        "Monthly harvest by product (cross-tab)",
        "harvests",
        "group=date&group=product__name&period=month&aggs=sum:quantity&pivot=1&chart=bar",
    ),
    ("Yearly harvest by product", "harvests", "group=date&group=product__name&period=year&aggs=sum:quantity&pivot=1"),
    ("Harvest per block & product", "harvests", "group=block__name&group=product__name&aggs=sum:quantity&pivot=1"),
    (
        "Harvest per person by day",
        "harvests",
        "cols=date&cols=product__name&cols=job_type__name&cols=quantity&cols=ind_people&cols=count_people"
        "&cols=ind_ot_hours&cols=count_ot_hours&cols=total_hours&cols=per_hour&cols=per_person_full_day"
        "&cols=per_person_ot&cols=per_person_full_day_ot&cols=per_person_average&cols=full_day_quantity"
        "&cols=ot_quantity&sort=-date",
    ),
    (
        "Labour cost by work type (individual)",
        "work-entries",
        "group=job_type__name&aggs=sum:total_amount&aggs=sum:ot_amount&aggs=count&sort=-agg&chart=bar",
    ),
    (
        "Daily labour count cost by work type per month",
        "group-work",
        "group=date&group=job_type__name&period=month&aggs=sum:total_cost&pivot=1",
    ),
    (
        "Daily labour count: man-days and cost per gang leader & month",
        "group-work",
        "group=gang_leader__name&group=date&period=month&aggs=sum:workers_count&aggs=sum:total_cost",
    ),
    (
        "Overtime by work type & month",
        "group-work",
        "group=date&group=job_type__name&period=month&aggs=sum:ot_amount&pivot=1",
    ),
    (
        "Fertilizer / chemical used per block",
        "chemical-use",
        "group=agri_input__name&group=operation__block__name&aggs=sum:quantity&pivot=1",
    ),
    (
        "Fertilizer / chemical cost per month (at the purchase prices used)",
        "chemical-use",
        "group=operation__date&group=agri_input__name&period=month&aggs=sum:cost&pivot=1",
    ),
    (
        "Fertilizer / chemical purchases per supplier",
        "chemical-purchases",
        "group=supplier&group=agri_input__name&aggs=sum:quantity&aggs=sum:total_amount",
    ),
    (
        "Field-work cost by operation per quarter",
        "field-operations",
        "group=date&group=operation_type__name&period=quarter&aggs=sum:total_cost&pivot=1",
    ),
    (
        "Spray & fertilizer history (list)",
        "field-operations",
        "cols=date&cols=operation_type__name&cols=block__name&cols=chemicals_text&cols=rounds&cols=input_cost"
        "&cols=purpose&cols=next_due_date&sort=-date",
    ),
    (
        "Processing recovery by product per month",
        "processing",
        "group=date&group=product__name&period=month&aggs=sum:input_quantity&aggs=sum:output_quantity"
        "&aggs=avg:recovery_percent&aggs=avg:conversion_ratio",
    ),
    (
        "Wage history by job type",
        "wage-rates",
        "cols=job_type__name&cols=effective_from&cols=effective_to&cols=rate&cols=ot_rate_per_hour"
        "&cols=employment_type__name&cols=remarks&sort=job_type__name",
    ),
    (
        "Daily labour count wage per worker by work type & month",
        "group-work",
        "group=date&group=job_type__name&period=month&aggs=avg:wage_per_worker&pivot=1&chart=line",
    ),
    (
        "Attendance count by employee & status",
        "attendance",
        "group=employee__full_name&group=status__name&aggs=count&pivot=1",
    ),
    ("Overtime hours by employee", "attendance", "group=employee__full_name&aggs=sum:ot_hours&sort=-agg"),
    (
        "Salary paid per month by job type",
        "salary-payments",
        "group=period_to&group=employee__job_type__name&period=month&aggs=sum:net_amount&pivot=1",
    ),
    (
        "Expenses by category per month",
        "expenses",
        "group=date&group=category__name&period=month&aggs=sum:amount&pivot=1",
    ),
    (
        "Sales revenue by product per quarter",
        "sales",
        "group=date&group=product__name&period=quarter&aggs=sum:total_amount&pivot=1",
    ),
    (
        "Average sale rate by product & month",
        "sales",
        "group=date&group=product__name&period=month&aggs=avg:rate&pivot=1&chart=line",
    ),
]
