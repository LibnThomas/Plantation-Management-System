import datetime as dt
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import DataError
from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core import dates
from core.exports import csv_response
from core.i18n import ui
from core.pdf import new_pdf
from estate import models as m
from estate.templatetags.estate_tags import inr, num
from estate.views import PDF_MAX_ROWS

from . import analytics as an
from .builder import DATASETS, PRESETS, agg_names, operators, params_from_querydict, period_names, run_report

D0 = Decimal(0)


def _floats(values):
    return [float(v or 0) for v in values]


def date_range(request, default_days=365):
    """Date range from the query string (explicit dates or a quick-range name)."""
    today = timezone.localdate()

    def parse(key):
        try:
            return dates.parse(request.GET.get(key, ""))
        except ValueError:
            return None

    d1, d2 = parse("date_from"), parse("date_to")
    presets = {
        "this_month": (today.replace(day=1), today),
        "this_fy": an.year_span(today, an.fy_month()),
        "last_fy": an.year_span(today, an.fy_month(), -1),
        "this_season": an.year_span(today, an.season_month()),
        "this_year": (dt.date(today.year, 1, 1), dt.date(today.year, 12, 31)),
        "last_12": ((today.replace(day=1) - dt.timedelta(days=335)).replace(day=1), today),
    }
    if request.GET.get("range") in presets:
        d1, d2 = presets[request.GET["range"]]
    d2 = d2 or today
    d1 = d1 or d2 - dt.timedelta(days=default_days)
    return (d2, d1) if d1 > d2 else (d1, d2)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@login_required
@an.shared_names()
def dashboard(request):
    today = timezone.localdate()
    month_start = today.replace(day=1)
    fy_start = an.year_span(today, an.fy_month())[0]
    season_start = an.year_span(today, an.season_month())[0]
    last12_start = (month_start - dt.timedelta(days=335)).replace(day=1)

    statuses = list(m.AttendanceStatus.objects.filter(is_active=True))
    counts = dict(m.Attendance.objects.filter(date=today).order_by().values_list("status").annotate(n=Count("id")))
    working_today = sum(n for sid, n in counts.items() if any(s.pk == sid and s.pay_fraction > 0 for s in statuses))
    products = {p.pk: p for p in m.Product.objects.select_related("unit")}
    season_harvest = [
        {"product": products.get(p), "q": q}
        for p, q in m.Harvest.objects.filter(date__gte=season_start)
        .values_list("product")
        .annotate(q=Sum("quantity"))
        .order_by("-q")
    ]
    season_processed = [
        {"product": products.get(p), "i": i, "o": o}
        for p, i, o in m.ProcessingBatch.objects.filter(date__gte=season_start)
        .values_list("product")
        .annotate(i=Sum("input_quantity"), o=Sum("output_quantity"))
        .order_by("-o")
    ]
    # one set of daily cost rows covers this month, the financial year and the last 12 months
    all_rows = an.cost_rows(min(month_start, fy_start, last12_start), today)
    month_rows = [r for r in all_rows if r["date"] >= month_start]
    _, month_cost = an.cost_summary(month_start, today, month_rows)
    labour_month_total = sum((Decimal(r["v"] or 0) for r in month_rows if r.get("labour")), D0)
    _, fy_cost = an.cost_summary(fy_start, today, [r for r in all_rows if r["date"] >= fy_start])
    prod = an.production(last12_start, today, "month")
    last12_rows = [r for r in all_rows if r["date"] >= last12_start]
    costs = an.series_by_period(last12_rows, last12_start, today, "month", series_key="cat")
    labour_fy, _ = an.labour_cost_by_job(fy_start, today)
    due = (
        m.FieldOperation.objects.filter(
            next_due_date__range=(today - dt.timedelta(days=30), today + dt.timedelta(days=21))
        )
        .select_related("block", "agri_input__unit", "operation_type")
        .order_by("next_due_date")[:8]
    )
    return render(
        request,
        "reports/dashboard.html",
        {
            "today": today,
            "season_start": season_start,
            "fy_start": fy_start,
            "active_employees": m.Employee.objects.filter(is_active=True).count(),
            "att_summary": [(s, counts.get(s.pk, 0)) for s in statuses],
            "working_today": working_today,
            "att_marked": sum(counts.values()),
            "month_cost": month_cost,
            "labour_month_total": labour_month_total,
            "season_harvest": season_harvest,
            "season_processed": season_processed,
            "fy_sales": m.Sale.objects.filter(date__gte=fy_start).aggregate(s=Sum("total_amount"))["s"] or 0,
            "fy_cost": fy_cost,
            "pending_salary": m.SalaryPayment.objects.filter(pay_state__in=("unpaid", "part"), balance__gt=0).aggregate(
                total=Sum("balance"), n=Count("id")
            ),
            "charts": {
                "prod": {"labels": prod["labels"], "series": prod["series"]},
                "cost": {"labels": costs["labels"], "series": costs["series"]},
                "labour": {
                    "labels": [x["name"] for x in labour_fy[:10]],
                    "data": _floats(x["amount"] for x in labour_fy[:10]),
                },
            },
            "due": due,
            "recent_ops": m.FieldOperation.objects.select_related(
                "block", "agri_input__unit", "operation_type"
            ).order_by("-date", "-id")[:6],
        },
    )


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
@an.shared_names()
def _analysis_data(request):
    d1, d2 = date_range(request)
    period = request.GET.get("period") if request.GET.get("period") in an.PERIODS else "month"
    if period == "day" and (d2 - d1).days > 120:
        period = "week"
    product = request.GET.get("product") if (request.GET.get("product") or "").isdigit() else None
    rows = an.cost_rows(d1, d2)
    cost_items, cost_total = an.cost_summary(d1, d2, rows)
    labour, labour_total = an.labour_cost_by_job(d1, d2)
    sales = an.sales_summary(d1, d2)
    sales_total = sum((s["amount"] or D0 for s in sales), D0)
    recovery = an.recovery_by_product(d1, d2)
    # cost per unit of the main processed product (the one with the most output); adding up kg of
    # different crops would mean nothing
    main = max(recovery, key=lambda r: r["output"], default=None)
    statuses, employees = an.employee_summary(d1, d2)
    return {
        "d1": d1,
        "d2": d2,
        "period": period,
        "product": product,
        "prod": an.production(d1, d2, period, product),
        "proc": an.processed_output(d1, d2, period, product),
        "cost_items": cost_items,
        "cost_total": cost_total,
        "costs_ts": an.series_by_period(rows, d1, d2, period, series_key="cat"),
        "labour": labour,
        "labour_total": labour_total,
        "sales": sales,
        "sales_total": sales_total,
        "profit": sales_total - cost_total,
        "recovery": recovery,
        "main_output": main,
        "cost_per_unit": cost_total / main["output"] if main and main["output"] else None,
        "inputs": an.input_usage(d1, d2),
        "last_ops": an.last_operation_per_block(),
        "blocks": an.block_performance(d1, d2),
        "statuses": statuses,
        "employees": employees,
        "mandays": an.workforce_mandays(d1, d2, period),
    }


@login_required
def analysis(request):
    data = _analysis_data(request)
    export = request.GET.get("export")
    if export == "pdf":
        return _analysis_pdf(data)
    if export == "employees":
        resp, w = csv_response(f"employee-summary-{data['d1']}-{data['d2']}.csv")
        w.writerow(
            [ui("Code"), ui("Employee"), ui("Job"), *[str(s) for s in data["statuses"]], ui("Man-days"), ui("OT hours")]
        )
        for e in data["employees"]:
            w.writerow(
                [e["emp"].employee_code, e["emp"].full_name, e["emp"].job_type, *e["counts"], e["mandays"], e["ot"]]
            )
        return resp
    data.update(
        {
            "periods": an.period_choices(),
            "products": m.Product.objects.all(),
            "charts": {
                "prod": {"labels": data["prod"]["labels"], "series": data["prod"]["series"]},
                "proc": {"labels": data["proc"]["labels"], "series": data["proc"]["series"]},
                "costs": {"labels": data["costs_ts"]["labels"], "series": data["costs_ts"]["series"]},
                "labour": {
                    "labels": [x["name"] for x in data["labour"]],
                    "data": _floats(x["amount"] for x in data["labour"]),
                },
                "mandays": {"labels": data["mandays"]["labels"], "series": data["mandays"]["series"]},
            },
        }
    )
    return render(request, "reports/analysis.html", data)


def _period_table(pdf, title, block):
    pdf.section(title)
    headers = [ui("Period"), *block["names"], ui("Total")]
    rows = [[r[0], *[num(v) for v in r[1:]]] for r in block["table"] if any(r[1:])]
    pdf.data_table(
        headers, rows, [False] + [True] * (len(headers) - 1), [ui("Total"), *[num(t) for t in block["totals"]]]
    )


def _analysis_pdf(d):
    pdf = new_pdf(ui("Analysis"), f"{dates.fmt(d['d1'])} – {dates.fmt(d['d2'])}")
    pdf.kpis(
        [
            (ui("Total cost"), inr(d["cost_total"], 0)),
            (ui("Sales revenue"), inr(d["sales_total"], 0)),
            (ui("Profit / (Loss)"), inr(d["profit"], 0)),
            (ui("Cost per unit of main product"), inr(d["cost_per_unit"]) if d["cost_per_unit"] else "–"),
        ]
    )
    _period_table(pdf, ui("Harvest (raw quantity)"), d["prod"])
    _period_table(pdf, ui("Processed output"), d["proc"])
    pdf.section(ui("Processing recovery by product"))
    pdf.data_table(
        [
            ui("Product"),
            ui("Batches"),
            ui("Raw input"),
            ui("Output"),
            ui("Recovery %"),
            ui("Ratio (in:out)"),
            ui("Processing cost / unit"),
        ],
        [
            [
                r["product"],
                r["batches"],
                num(r["input"]),
                f"{num(r['output'])} {r['unit']}",
                f"{r['recovery']:.2f}",
                f"{r['ratio']:.2f} : 1",
                inr(r["cost_per_unit"]),
            ]
            for r in d["recovery"]
        ],
        [False] + [True] * 6,
    )
    pdf.section(ui("Cost breakdown"))
    pdf.data_table(
        [ui("Category"), ui("Amount"), ui("Share")],
        [[c["name"], inr(c["amount"], 0), f"{c['pct']:.1f}%"] for c in d["cost_items"]],
        [False, True, True],
        [ui("Total"), inr(d["cost_total"], 0), ""],
    )
    pdf.section(ui("Labour cost by work type"))
    pdf.data_table(
        [ui("Work type"), ui("Amount"), ui("Share")],
        [[x["name"], inr(x["amount"], 0), f"{x['pct']:.1f}%"] for x in d["labour"]],
        [False, True, True],
        [ui("Total"), inr(d["labour_total"], 0), ""],
    )
    pdf.section(ui("Sales by product"))
    pdf.data_table(
        [ui("Product"), ui("Quantity"), ui("Revenue"), ui("Average rate")],
        [[s["name"], num(s["qty"]), inr(s["amount"], 0), inr(s["avg_rate"])] for s in d["sales"]],
        [False, True, True, True],
    )
    pdf.section(ui("Fertilizer / chemical usage"))
    pdf.data_table(
        [ui("Fertilizer / chemical"), ui("Applications"), ui("Quantity"), ui("Cost"), ui("Last used")],
        [
            [i["name"], i["n"], f"{num(i['qty'])} {i['unit']}", inr(i["cost"], 0), dates.fmt(i["last"])]
            for i in d["inputs"]
        ],
        [False, True, True, True, False],
    )
    lo = d["last_ops"]
    if lo["ops"]:
        pdf.section(ui("Last done per block"))
        pdf.data_table(
            [ui("Block"), *[name for _, name in lo["ops"]]],
            [[b, *[dates.fmt(v) or "–" for v in vals]] for b, vals in lo["rows"]],
        )
    pdf.section(ui("Block / plot performance"))
    pdf.data_table(
        [ui("Block"), ui("Area (acres)"), ui("Main crop harvest"), ui("Yield / acre"), ui("Cost"), ui("Cost / acre")],
        [
            [
                b["block"].name,
                num(b["block"].area_acres),
                num(b["total_qty"]),
                num(b["yield_per_acre"]) if b["yield_per_acre"] is not None else "–",
                inr(b["cost"], 0),
                inr(b["cost_per_acre"], 0) if b["cost_per_acre"] is not None else "–",
            ]
            for b in d["blocks"]
        ],
        [False] + [True] * 5,
    )
    pdf.section(ui("Employee attendance summary"))
    pdf.data_table(
        [ui("Employee"), *[str(s) for s in d["statuses"]], ui("Man-days"), ui("OT hours")],
        [[e["emp"].full_name, *e["counts"], num(e["mandays"]), num(e["ot"])] for e in d["employees"]],
        [False] + [True] * (len(d["statuses"]) + 2),
    )
    return pdf.response(f"analysis-{d['d1']}-{d['d2']}.pdf")


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------
def _builder_export(ds, result, kind):
    if result.get("pivot"):
        p = result["pivot"]
        headers = [p["corner"], *[str(c) for c in p["cols"]], ui("Total")]
        rows = [[r[0], *r[1:]] for r in p["rows"]]
        totals = [ui("Total"), *p["col_totals"]]
        numeric = [False] + [True] * (len(headers) - 1)
    else:
        headers, rows, numeric = result["headers"], result["rows"], result["numeric"]
        totals = None
        if any(t is not None for t in result["totals"]):
            totals = ["" if t is None else t for t in result["totals"]]
            totals[0] = totals[0] or ui("Total")
    stamp = timezone.localdate()
    if kind == "pdf":

        def fmt(v, is_num):
            return num(v) if is_num and v not in (None, "") else ("" if v is None else str(v))

        pdf = new_pdf(ds.title, ui("Report Builder"), columns=len(headers))
        pdf.data_table(
            headers,
            [[fmt(v, n) for v, n in zip(r, numeric, strict=True)] for r in rows],
            numeric,
            [fmt(v, n) for v, n in zip(totals, numeric, strict=True)] if totals else None,
        )
        if result.get("total_count", 0) > result.get("shown", 0):
            pdf.note(ui("showing the first {n}; export CSV for all", n=result["shown"]))
        return pdf.response(f"report-{ds.slug}-{stamp}.pdf")
    resp, w = csv_response(f"report-{ds.slug}-{stamp}.csv")
    w.writerow(headers)
    w.writerows(rows)
    if totals:
        w.writerow(totals)
    return resp


@login_required
def builder(request):
    if request.GET.get("saved", "").isdigit():
        sr = get_object_or_404(m.SavedReport, pk=int(request.GET["saved"]))
        return redirect(f"{reverse('reports:builder')}?{sr.params.get('qs', '')}")
    ds = DATASETS.get(request.GET.get("ds")) or next(iter(DATASETS.values()))
    params = params_from_querydict(request.GET)
    ran = any(k in request.GET for k in ("run", "export", "cols", "group", "f_field"))
    result = None
    if ran:
        export = request.GET.get("export")
        try:
            if export in ("csv", "pdf"):
                return _builder_export(
                    ds, run_report(ds, params, limit=PDF_MAX_ROWS if export == "pdf" else None), export
                )
            result = run_report(ds, params, limit=1000)
        except (ValidationError, ValueError, DataError):
            messages.error(request, ui("This report could not be run. Please check the filters."))
            result = None
    if result:
        for e in result["errors"]:
            messages.warning(request, e)
    if not params["cols"] and not params["group"]:
        params["cols"] = ds.default_columns
    qs = request.GET.copy()
    qs.pop("export", None)
    return render(
        request,
        "reports/builder.html",
        {
            "datasets": DATASETS,
            "ds": ds,
            "fields": ds.fields,
            "params": params,
            "result": result,
            "builder_data": {
                "fields": ds.json_fields(),
                "operators": operators(),
                "filters": params["filters"],
                "aggs": params["aggs"],
                "agg_funcs": list(agg_names().items()),
                "mode": "group" if params["group"] else "list",
            },
            "chart_data": result["chart"] if result and result.get("chart") else None,
            "periods": list(period_names().items()),
            "presets": [(ui(name), slug, q) for name, slug, q in PRESETS],
            "saved_reports": m.SavedReport.objects.all(),
            "querystring": qs.urlencode(),
        },
    )


@login_required
@require_POST
def save_report(request):
    name = (request.POST.get("name") or "").strip()
    qs = request.POST.get("qs", "")
    if not name:
        messages.error(request, ui("Please give the report a name."))
    else:
        ds = dict(p.split("=", 1) for p in qs.split("&") if "=" in p).get("ds", "")
        m.SavedReport.objects.create(
            name=name[:120],
            dataset=ds[:40],
            params={"qs": qs},
            created_by=request.user,
            description=request.POST.get("description", "")[:250],
        )
        messages.success(request, ui("Report '{name}' saved.", name=name))
    return redirect(f"{reverse('reports:builder')}?{qs}")


@login_required
@require_POST
def delete_report(request, pk):
    report = get_object_or_404(m.SavedReport, pk=pk)
    if not (request.user.is_staff or report.created_by_id == request.user.pk):
        messages.error(request, ui("Only the person who saved this report can delete it."))
        return redirect("reports:builder")
    report.delete()
    messages.success(request, ui("Saved report deleted."))
    return redirect("reports:builder")
