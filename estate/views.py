import calendar
import datetime as dt
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, router, transaction
from django.db.models import Count, ProtectedError, Q, Sum
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from core import appsettings, dates
from core.exports import csv_stream
from core.i18n import N_, field_label, model_label, module_title, ui
from core.pdf import new_pdf

from . import deletion, payroll, productivity
from . import models as m
from .forms import (
    PayrollGenerateForm,
    build_chemical_formset,
    build_form_class,
    build_purchase_formset,
    build_wage_history_formset,
    style_fields,
)
from .registry import BY_MODEL, LOOKUP_MODULES
from .registry import get as get_cfg
from .templatetags.estate_tags import NUMERIC_TYPES, cell_value, inr, num

PAGE_SIZE = 25
PAGE_SIZES = (25, 50, 100, 250, 500)  # rows per page the user can pick (the in-browser search works on these)
PDF_MAX_ROWS = 2000  # bigger lists: CSV (a PDF of thousands of pages helps nobody and ties up the server)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _cfg_or_404(slug):
    cfg = get_cfg(slug)
    if not cfg:
        raise Http404
    return cfg


def _edit_denied(request, cfg):
    """A redirect with a message when this user may only view this module, else None."""
    if cfg.can_edit(request.user):
        return None
    messages.error(request, ui("Only an administrator can change this list."))
    return redirect("estate:list", slug=cfg.slug)


def parse_date(value):
    return dates.parse(value)


def apply_filters(cfg, request, qs, with_widgets=False):
    """Search / date range / drop-down filters from the query string."""
    params = request.GET
    q = params.get("q", "").strip()
    if q and cfg.search_fields:
        cond = Q()
        for f in cfg.search_fields:
            cond |= Q(**{f"{f}__icontains": q})
        qs = qs.filter(cond)
    if cfg.date_field:
        d_from, d_to = parse_date(params.get("date_from")), parse_date(params.get("date_to"))
        if d_from:
            qs = qs.filter(**{f"{cfg.date_field}__gte": d_from})
        if d_to:
            qs = qs.filter(**{f"{cfg.date_field}__lte": d_to})
    widgets = []
    for name in cfg.filter_fields:
        f = cfg.model._meta.get_field(name)
        selected = params.get(name, "")
        if f.is_relation:
            if selected.isdigit():
                # many-to-many (a payroll's plots): records that include it
                qs = qs.filter(**{f"{name}__id" if f.many_to_many else f"{name}_id": int(selected)})
            options = [(str(o.pk), str(o)) for o in related_options(f.related_model)] if with_widgets else []
        elif f.choices:
            if selected == "pending" and name == "pay_state":  # payroll: not paid + part paid
                qs = qs.filter(**{f"{name}__in": ("unpaid", "part")})
            elif selected == "open" and name == "receipt_state":  # sales: nothing or only part received
                qs = qs.filter(**{f"{name}__in": ("pending", "part")})
            elif selected:
                qs = qs.filter(**{name: selected})
            options = [(k, ui(str(v))) for k, v in f.choices]
            if name == "pay_state":
                options.insert(0, ("pending", ui("Pending (not paid + part paid)")))
            elif name == "receipt_state":
                options.insert(0, ("open", ui("Still to receive (pending + partially received)")))
        else:  # boolean
            if selected in ("1", "0"):
                qs = qs.filter(**{name: selected == "1"})
            options = [("1", ui("Yes")), ("0", ui("No"))]
        if with_widgets:
            widgets.append(
                {"name": name, "label": field_label(cfg.model, name), "options": options, "selected": selected}
            )
    return qs, widgets


def columns_for(model, names):
    return [
        {
            "name": n,
            "label": field_label(model, n),
            "numeric": model._meta.get_field(n).get_internal_type() in NUMERIC_TYPES,
        }
        for n in names
    ]


def _str_related(model):
    """Relations a model's __str__ reads (e.g. a fertilizer shows its unit)."""
    return getattr(model, "STR_RELATED", ())


def related_options(model):
    return model.objects.select_related(*_str_related(model))


def with_related(model, names):
    related = []
    for name in names:
        related.append(name)
        related += [f"{name}__{x}" for x in _str_related(model._meta.get_field(name).related_model)]
    qs = model.objects.select_related(*_str_related(model))
    return qs.select_related(*related) if related else qs


def base_queryset(cfg):
    fields = [cfg.model._meta.get_field(f) for f in cfg.list_fields]
    qs = with_related(cfg.model, [f.name for f in fields if f.is_relation and not f.many_to_many])
    many = [f.name for f in fields if f.many_to_many]  # e.g. a payroll's plots: one extra query per page
    return qs.prefetch_related(*many) if many else qs


def totals_for(cfg, qs):
    if not cfg.sum_fields:
        return {}
    return {k: (v or 0) for k, v in qs.aggregate(**{f: Sum(f) for f in cfg.sum_fields}).items()}


def filter_summary(request):
    """Readable 'From … To … Search …' line for PDF headers."""
    g, parts = request.GET, []
    if g.get("date_from"):
        parts.append(f"{ui('From')} {g['date_from']}")
    if g.get("date_to"):
        parts.append(f"{ui('To')} {g['date_to']}")
    if g.get("q"):
        parts.append(f"{ui('Search')}: {g['q']}")
    return "  ·  ".join(parts)


# ---------------------------------------------------------------------------
# Generic list / form / delete / exports
# ---------------------------------------------------------------------------
def _date_text(value):
    """A date filter as typed, shown as DD/MM/YY when it is a date."""
    return dates.fmt(dates.parse(value)) or (value or "")


def _url_maker(name, slug):
    """pk -> URL, resolved once per page instead of once per row."""
    head, _, tail = reverse(name, args=[slug, 0]).rpartition("/0/")
    return lambda pk: f"{head}/{pk}/{tail}"


@login_required
def crud_list(request, slug):
    cfg = _cfg_or_404(slug)
    qs, filter_widgets = apply_filters(cfg, request, base_queryset(cfg), with_widgets=True)
    totals = totals_for(cfg, qs)
    per_page = request.GET.get("per_page", "")
    per_page = int(per_page) if per_page.isdigit() and int(per_page) in PAGE_SIZES else PAGE_SIZE
    page = Paginator(qs, per_page).get_page(request.GET.get("page"))
    columns = columns_for(cfg.model, cfg.list_fields)
    edit_url, delete_url = _url_maker("estate:update", slug), _url_maker("estate:delete", slug)
    rows = [
        {
            "obj": o,
            "cells": [(cell_value(o, c["name"]), c["numeric"]) for c in columns],
            "edit_url": edit_url(o.pk),
            "delete_url": delete_url(o.pk),
        }
        for o in page.object_list
    ]
    return render(
        request,
        "estate/list.html",
        {
            "cfg": cfg,
            "title": module_title(cfg),
            "page": page,
            "rows": rows,
            "columns": columns,
            "filter_widgets": filter_widgets,
            "has_totals": bool(totals),
            "total_cells": [totals.get(c["name"]) for c in columns],
            "count": page.paginator.count,
            "active_filters": any(v for k, v in request.GET.items() if k != "page"),
            "per_page": per_page,
            "date_from_text": _date_text(request.GET.get("date_from")),
            "date_to_text": _date_text(request.GET.get("date_to")),
            "page_sizes": PAGE_SIZES,
            "is_lookup": cfg.group == "Lists",
            "record_name": model_label(cfg.model),
            "can_edit": cfg.can_edit(request.user),
        },
    )


def _add_new_targets(request, FormClass, popup):
    """Drop-downs that get an "Add new" entry: the related list has its own screen and this user may
    add to it. Not inside a pop-up (one level is enough)."""
    if popup:
        return {}
    out = {}
    for name, field in FormClass.base_fields.items():
        model = getattr(getattr(field, "queryset", None), "model", None)
        rel_cfg = BY_MODEL.get(model)
        if rel_cfg and rel_cfg.can_edit(request.user):
            out[name] = {
                "url": f"{reverse('estate:create', args=[rel_cfg.slug])}?popup=1",
                "title": ui("Add new: {name}", name=model_label(model)),
            }
    return out


def popup_payload(obj):
    """What the page that opened an "Add new" pop-up needs about the new record: its id and name, and
    the values its form uses to fill other fields (cost per unit, wage, unit ...)."""
    attrs = {}
    for f in obj._meta.concrete_fields:
        if f.get_internal_type() in ("BinaryField", "JSONField") or f.name in (
            "created_by",
            "created_at",
            "updated_at",
        ):
            continue
        value = getattr(obj, f.attname)
        attrs[f.attname] = "" if value is None else str(value)
    if getattr(obj, "unit_id", None):
        attrs["unit"] = str(obj.unit)
    if isinstance(obj, m.JobType):
        attrs["history"] = [_rate_row(r) for r in obj.wage_rates.order_by("-effective_from", "-id")]
    return {"type": "pms-added", "id": str(obj.pk), "label": str(obj), "attrs": attrs}


def _rate_row(r):
    """One wage period for the form's rate auto-fill (same choice as models.pick_rate)."""
    return {
        "from": r.effective_from.isoformat(),
        "to": r.effective_to.isoformat() if r.effective_to else None,
        "type": r.employment_type_id,
        "block": r.block_id,
        "emp": r.employee_id,
        "workers": r.workers,
        "wage": str(r.rate),
        "ot": str(r.ot_rate_per_hour),
    }


def _form_context_data(cfg, FormClass):
    """Data the form page needs to pre-fill rates / costs when something is picked."""
    data = {}
    if cfg.job_defaults:
        history = {}
        for r in m.WageRate.objects.order_by("-effective_from", "-id"):
            history.setdefault(str(r.job_type_id), []).append(_rate_row(r))
        data["job"] = {
            "fields": cfg.job_defaults,
            "is_count": cfg.model is m.GroupWorkEntry,  # Daily Labour Count: rates for counts apply
            "employees": (
                {
                    str(pk): {"job": str(job), "type": emp_type}
                    for pk, job, emp_type in m.Employee.objects.values_list("pk", "job_type_id", "employment_type_id")
                }
                if "employee" in FormClass.base_fields
                else {}
            ),
            "jobs": {
                str(j.pk): {
                    "wage": str(j.default_daily_wage),
                    "ot": str(j.default_ot_rate),
                    "history": history.get(str(j.pk), []),
                }
                for j in m.JobType.objects.all()
            },
        }
    if cfg.fk_defaults:
        fk = {}
        for source, targets in cfg.fk_defaults.items():
            rel = cfg.model._meta.get_field(source).related_model
            qs = rel.objects.select_related("unit") if hasattr(rel, "unit") else rel.objects.all()
            fk[source] = {
                "targets": targets,
                "rows": {
                    str(o.pk): {
                        **{attr: str(getattr(o, attr)) for attr in targets.values()},
                        "unit": str(o.unit) if getattr(o, "unit_id", None) else "",
                    }
                    for o in qs
                },
            }
        data["fk"] = fk
    if cfg.fk_narrow:
        trigger, target, attr = cfg.fk_narrow
        t_model = cfg.model._meta.get_field(trigger).related_model
        g_model = cfg.model._meta.get_field(target).related_model
        data["narrow"] = {
            "trigger": trigger,
            "target": target,
            "attr": attr,
            "trigger_values": {str(pk): v for pk, v in t_model.objects.values_list("pk", attr)},
            "target_values": {str(pk): v for pk, v in g_model.objects.values_list("pk", attr)},
        }
    return data


def _harvest_preview_data(cfg, instance):
    """Harvest form: where to fetch the day's people from, and each product's unit and picking job."""
    if cfg.model is not m.Harvest:
        return {}
    return {
        "harvest": {
            "url": reverse("estate:harvest_people"),
            "pk": instance.pk if instance else None,
            "products": {
                str(p.pk): {"unit": str(p.unit) if p.unit_id else "", "job": p.harvest_job_type_id}
                for p in m.Product.objects.select_related("unit")
            },
        }
    }


def _work_entries_for_many(request, cfg, ids):
    """One work entry per selected employee, for the date and block typed. Each gets the employee's own work
    type, and the wage and OT rate in force for it on that date (exactly as a single entry left as filled in)."""
    back = reverse("estate:create", args=[cfg.slug])
    day = dates.parse(request.POST.get("date"))
    if not day:
        messages.error(
            request, ui("Enter a date as DD/MM/YY, e.g. {example}.", example=dates.fmt(timezone.localdate()))
        )
        return redirect(back)
    block_id = request.POST.get("block", "")
    block = m.Block.objects.filter(pk=int(block_id)).first() if block_id.isdigit() else None
    employees = list(m.Employee.objects.filter(pk__in=ids).select_related("job_type"))
    with transaction.atomic(), productivity.paused():  # harvest figures of the day re-worked once
        for emp in employees:
            m.WorkEntry(date=day, employee=emp, job_type=emp.job_type, block=block, created_by=request.user).save()
    messages.success(request, ui("Saved {n} work entries for {date}.", n=len(employees), date=dates.fmt(day)))
    if "save_add" in request.POST:
        return redirect(back)
    return redirect("estate:list", slug=cfg.slug)


def _multi_employee_data(cfg, instance, popup):
    """Work Details - Individual (adding, not in a pop-up): the employees that can be picked together."""
    if cfg.model is not m.WorkEntry or instance or popup:
        return {}
    return {
        "multi": {
            "employees": [
                {"id": e.pk, "name": e.full_name, "code": e.employee_code, "job": str(e.job_type)}
                for e in m.Employee.objects.filter(is_active=True).select_related("job_type").order_by("full_name")
            ]
        }
    }


@login_required
def crud_form(request, slug, pk=None):
    cfg = _cfg_or_404(slug)
    if denied := _edit_denied(request, cfg):
        return denied
    instance = get_object_or_404(cfg.model, pk=pk) if pk else None
    if not instance and not cfg.can_add:
        return redirect("estate:list", slug=slug)
    if instance and cfg.model is m.FieldOperationInput:  # a chemical line is changed on its field-work entry
        return redirect("estate:update", slug="field-operations", pk=instance.operation_id)
    FormClass = build_form_class(cfg)
    initial = {}
    if not instance:  # pre-fill from the query string, e.g. ?employee=3&date=2026-10-01
        initial = {k: v for k, v in request.GET.items() if k in FormClass.base_fields}
    data = request.POST or None
    if request.method == "POST" and cfg.model is m.WorkEntry and not instance:
        # Work Details - Individual: several employees picked at once
        ids = [i for i in request.POST.getlist("employees") if i.isdigit()]
        if len(ids) > 1:
            return _work_entries_for_many(request, cfg, ids)
        if len(ids) == 1:  # one employee: the normal form, every value can be changed
            data = request.POST.copy()
            data["employee"] = ids[0]
    form = FormClass(data, instance=instance, initial=initial)
    paying = cfg.model is m.SalaryPayment and not request.GET.get("popup")
    if paying:
        _add_payment_field(form, instance)
    receiving = cfg.model is m.Sale and not request.GET.get("popup")
    if receiving:
        _add_receipt_fields(form, instance)
    for name in getattr(instance, "locked_fields", lambda: ())():
        if name in form.fields:
            form.fields[name].disabled = True
            form.fields[name].help_text = ui("Calculated by Generate Payroll. Re-generate to change it.")

    history = None
    if cfg.has_wage_history:
        has_rows = instance is not None and instance.wage_rates.exists()
        history = build_wage_history_formset(extra=1 if has_rows else 2)(
            data,
            instance=instance or cfg.model(),
            prefix="rates",
            queryset=m.WageRate.objects.order_by("-effective_from") if instance else m.WageRate.objects.none(),
        )

    popup = request.GET.get("popup") == "1"
    # fertilizers / chemicals: their purchases; field work: the chemicals used
    lines = None
    if cfg.model is m.AgriInput and not popup:
        lines = build_purchase_formset()(
            data,
            instance=instance or m.AgriInput(),
            prefix="buy",
            queryset=m.AgriInputPurchase.objects.order_by("date", "id")
            if instance
            else m.AgriInputPurchase.objects.none(),
        )
    elif cfg.model is m.FieldOperation and not popup:
        lines = build_chemical_formset()(
            data,
            instance=instance or m.FieldOperation(),
            prefix="chem",
            queryset=m.FieldOperationInput.objects.select_related("agri_input__unit").order_by("id")
            if instance
            else m.FieldOperationInput.objects.none(),
        )
    if (
        request.method == "POST"
        and form.is_valid()
        and (history is None or history.is_valid())
        and (lines is None or lines.is_valid())
    ):
        try:
            with transaction.atomic():
                obj = form.save(commit=False)
                if not obj.pk:
                    obj.created_by = request.user
                obj.save()
                form.save_m2m()
                if paying:
                    _save_current_payment(request, form, obj)
                if receiving:
                    _save_receipt(request, form, obj)
                if lines is not None:
                    _save_lines(request, lines, obj)
            if history is not None:
                history.instance = obj
                for r in history.save(commit=False):
                    r.created_by_id = r.created_by_id or request.user.pk
                    r._close_later = True  # closed once below, after all rows are saved
                    r.save()
                for r in history.deleted_objects:
                    r.delete()
                m.close_open_rates(obj.pk)
        except ValidationError as e:  # e.g. paying more than is pending: nothing is saved
            form.add_error("received_now" if receiving else "current_payment", e.messages[0])
            return _render_form(request, cfg, form, instance, history, FormClass, popup, lines)
        if popup:  # hand the new record to the page that opened the pop-up
            return render(request, "estate/popup_done.html", {"payload": popup_payload(obj)})
        messages.success(request, ui("Saved: {name}", name=model_label(cfg.model)))
        if cfg.model is m.FieldOperation:
            _stock_warnings(request, obj)
        if paying and form.cleaned_data.get("current_payment"):
            obj.refresh_from_db()
            text = (
                "Payment of {amount} saved as Paid. Balance (pending): {balance}."
                if form.cleaned_data["status"].is_final
                else "Payment of {amount} saved as Pending (not paid) - it is not counted as paid. "
                "Balance (pending): {balance}."
            )
            messages.info(request, ui(text, amount=inr(form.cleaned_data["current_payment"]), balance=inr(obj.balance)))
        if receiving and form.cleaned_data.get("received_now"):
            obj.refresh_from_db()
            messages.info(
                request,
                ui(
                    "Payment received of {amount} saved. Still to receive: {balance}.",
                    amount=inr(form.cleaned_data["received_now"]),
                    balance=inr(obj.balance),
                ),
            )
        if "save_add" in request.POST:
            return redirect("estate:create", slug=slug)
        return redirect("estate:list", slug=slug)
    return _render_form(request, cfg, form, instance, history, FormClass, popup, lines)


def _save_lines(request, formset, parent):
    """Purchases of a chemical, or the chemicals of a field-work entry (each line recalculates its
    purchase, the chemical's stock and the entry's cost when saved / deleted)."""
    formset.instance = parent
    for line in formset.save(commit=False):
        if not line.pk:
            line.created_by = request.user
        line.save()
    for line in formset.deleted_objects:
        line.delete()
    if isinstance(parent, m.FieldOperation):
        parent.update_costs()
    else:
        parent.refresh_stock()


def _stock_warnings(request, op):
    for line in op.chemicals.select_related("agri_input__unit", "purchase"):
        a = line.agri_input
        a.refresh_from_db(fields=["stock_qty"])
        if a.stock_qty < 0:
            messages.warning(
                request,
                ui(
                    "{name}: more used than bought (stock {qty}). Enter its purchase in Fertilizers / Chemicals.",
                    name=a.name,
                    qty=f"{num(a.stock_qty)} {a.unit_name}".strip(),
                ),
            )
        elif line.purchase_id and line.purchase.left_qty < 0:
            messages.warning(
                request,
                ui(
                    "{name}: more used from the purchase of {date} than it had ({qty} short). "
                    "Pick another purchase for the rest, or check the quantity.",
                    name=a.name,
                    date=f"{line.purchase.date:%d/%m/%y}",
                    qty=f"{num(-line.purchase.left_qty)} {a.unit_name}".strip(),
                ),
            )


def _add_payment_field(form, instance):
    """Payroll edit page: 'Current payment amount' (paid now; part payments allowed)."""
    from django import forms as dj_forms

    pending = instance.balance if instance else None
    field = dj_forms.DecimalField(
        required=False, min_value=0, max_digits=12, decimal_places=2, label=ui("Current payment amount")
    )
    field.help_text = ui(
        "What you pay now (all or part). Saved as a payment on the payment date and mode given; the rest stays pending."
    )
    field.widget.attrs.update({"inputmode": "decimal", "step": "any", "class": "form-control current-payment"})
    if pending is not None and (pending <= 0 or instance.carried_to_id):
        field.disabled = True
        field.help_text = ui("Nothing is pending on this payroll.")
    elif not form.is_bound:  # a new payment is usually made today
        form.initial["payment_date"] = timezone.localdate()
    # right after the deductions, before the payment date / mode
    names = list(form.fields)
    form.fields["current_payment"] = field
    at = names.index("payment_date") if "payment_date" in names else len(names)
    form.order_fields([*names[:at], "current_payment", *names[at:]])
    for name, label in (("payment_date", "Payment date"), ("payment_mode", "Payment mode")):
        if name in form.fields:
            form.fields[name].help_text = ui("For the current payment")
            form.fields[name].label = field_label(m.SalaryPayment, name) or ui(label)


def _save_current_payment(request, form, salary):
    amount = form.cleaned_data.get("current_payment") or Decimal(0)
    if amount <= 0:
        return
    pay = m.SalaryPaid(
        salary=salary,
        date=salary.payment_date or timezone.localdate(),
        amount=amount,
        payment_mode=salary.payment_mode,
        # the payroll status chosen on the form: 'Paid' = paid now, 'Pending' = recorded but not paid yet
        status="paid" if salary.status.is_final else "pending",
        created_by=request.user,
    )
    pay.full_clean()  # more than pending: ValidationError, the whole save is undone
    pay.save()


def _add_receipt_fields(form, instance):
    """Sale add / edit page: 'Amount received now', its received date and mode (part payments allowed)."""
    from django import forms as dj_forms

    pending = instance.balance if instance else None
    amount = dj_forms.DecimalField(
        required=False, min_value=0, max_digits=12, decimal_places=2, label=ui("Amount received now")
    )
    amount.help_text = ui("Money received now (all or part). Saved as a payment received; the rest stays to receive.")
    amount.widget.attrs["class"] = "received-now"
    day = dj_forms.DateField(required=False, label=ui("Payment received date"), initial=timezone.localdate)
    mode = dj_forms.ModelChoiceField(
        m.PaymentMode.objects.filter(is_active=True), required=False, label=ui("Payment mode")
    )
    mode.empty_label = "— " + ui("choose") + " —"
    remarks = dj_forms.CharField(
        required=False, max_length=200, label=ui("Payment remarks"), help_text=ui("e.g. cheque / UTR no.")
    )
    extra = {"received_now": amount, "received_date": day, "received_mode": mode, "received_remarks": remarks}
    if pending is not None and pending <= 0 and instance.amount_received:
        for f in extra.values():
            f.disabled = True
        amount.help_text = ui("The full amount is received.")
    style_fields(extra)
    names = list(form.fields)
    form.fields.update(extra)
    at = names.index("remarks") if "remarks" in names else len(names)
    form.order_fields([*names[:at], *extra, *names[at:]])


def _save_receipt(request, form, sale):
    amount = form.cleaned_data.get("received_now") or Decimal(0)
    if amount <= 0:
        return
    receipt = m.SaleReceipt(
        sale=sale,
        date=form.cleaned_data.get("received_date") or timezone.localdate(),
        amount=amount,
        payment_mode=form.cleaned_data.get("received_mode"),
        remarks=form.cleaned_data.get("received_remarks") or "",
        created_by=request.user,
    )
    try:
        receipt.full_clean()  # more than is still to receive: the whole save is undone
    except ValidationError as e:
        raise ValidationError(" ".join(e.messages)) from e
    receipt.save()


def _sale_edit_data(cfg, instance):
    """Sale add / edit page: figures for the live 'payments received' summary on the right."""
    if cfg.model is not m.Sale:
        return {}
    s = instance
    data = {
        "sale_live": {
            "rate": float(s.rate) if s else 0,
            "received": float(s.amount_received) if s else 0,
        }
    }
    if s:
        data["sale"] = s
        data["receipts"] = list(s.receipts.select_related("payment_mode").order_by("date", "id"))
        data["days_since_sold"] = (timezone.localdate() - s.date).days
    return data


def _render_form(request, cfg, form, instance, history, FormClass, popup, lines=None):
    computed = []
    if instance and cfg.model not in (m.Harvest, m.SalaryPayment, m.Sale):  # these show live figures instead
        computed = [
            (field_label(cfg.model, f.name), cell_value(instance, f.name))
            for f in cfg.model._meta.fields
            if not f.editable and f.name not in ("id", "created_at", "updated_at", "created_by", "breakdown")
        ]
    return render(
        request,
        "estate/form.html",
        {
            "cfg": cfg,
            "title": module_title(cfg),
            "record_name": model_label(cfg.model),
            "form": form,
            "instance": instance,
            "computed": computed,
            "history": history,
            "lines": lines,
            "skip_fields": MIX_FIELDS if cfg.model is m.FieldOperation and lines is not None else (),
            "form_data": {
                **_form_context_data(cfg, FormClass),
                "add_new": _add_new_targets(request, FormClass, popup),
                **_harvest_preview_data(cfg, instance),
                **_multi_employee_data(cfg, instance, popup),
                **_chemicals_data(cfg, instance, lines),
            },
            "popup": popup,
            **_payroll_edit_data(cfg, instance),
            **_sale_edit_data(cfg, instance),
        },
    )


MIX_FIELDS = ("mix_type", "tank_litres", "mix_dose", "packet_size", "rounds", "mix_name")


def _chemicals_data(cfg, instance, lines):
    """Field-work page: every fertilizer / chemical with its dosage, stock and purchases (prices), for the
    search box, the auto quantities and costs, the stock warnings and the mixing preview."""
    if cfg.model is not m.FieldOperation or lines is None:
        return {}
    own_purchase, own_input = {}, {}  # what this entry already uses: available again while editing it
    if instance:
        for x in instance.chemicals.all():
            own_purchase[x.purchase_id] = own_purchase.get(x.purchase_id, 0) + x.quantity
            own_input[x.agri_input_id] = own_input.get(x.agri_input_id, 0) + x.quantity
    purchases = {}
    for p in m.AgriInputPurchase.objects.select_related("agri_input__unit").order_by("-date", "-id"):
        purchases.setdefault(p.agri_input_id, []).append(
            {
                "id": p.pk,
                "label": p.option_label(),
                "date": p.date.isoformat(),
                "cost": float(p.unit_cost),
                "left": float(p.left_qty + own_purchase.get(p.pk, 0)),
            }
        )
    chems = []
    for a in m.AgriInput.objects.select_related("unit", "input_type").order_by("name"):
        chems.append(
            {
                "id": a.pk,
                "name": a.name,
                "unit": a.unit_name,
                "type": a.input_type_id,
                "type_name": str(a.input_type) if a.input_type_id else "",
                "active": a.is_active,
                "dose": float(a.dose_qty),
                "basis": a.dose_basis,
                "water": float(a.dose_water),
                "dosage": a.dosage_text(),
                "pack": float(a.pack_size),
                "cost": float(a.unit_cost),
                "stock": float(a.stock_qty + own_input.get(a.pk, 0)),
                "purchases": purchases.get(a.pk, []),
            }
        )
    op_types = dict(m.OperationType.objects.exclude(input_type=None).values_list("pk", "input_type"))
    # a new entry starts with the jar size used last time (or 500 L)
    last_jar = (
        m.FieldOperation.objects.filter(tank_litres__gt=0)
        .order_by("-date", "-id")
        .values_list("tank_litres", flat=True)
        .first()
    )
    return {
        "chem": {
            "items": chems,
            "op_types": op_types,
            "saved": _saved_mixes(),
            "default_jar": float(last_jar or 500),
        },
        "labour_cost": float(instance.labour_cost) if instance else 0,
    }


def _saved_mixes(limit=60):
    """Mixes used before (newest first, each different mix once), to fill a new entry with the same
    chemicals and amounts. A mix with a name shows its name first."""
    out, seen = [], set()
    ops = (
        m.FieldOperation.objects.filter(chemicals__isnull=False)
        .distinct()
        .select_related("operation_type")
        .prefetch_related("chemicals__agri_input__unit")
        .order_by("-date", "-id")[:400]
    )
    for op in ops:
        lines = sorted(op.chemicals.all(), key=lambda x: x.pk)
        key = (
            op.mix_type,
            float(op.tank_litres),
            tuple(sorted((x.agri_input_id, float(x.dose_qty), x.dose_basis) for x in lines)),
        )
        if key in seen:
            continue
        seen.add(key)
        parts = " + ".join(f"{x.agri_input.name} {num(x.dose_qty)} {x.agri_input.unit_name}".strip() for x in lines)
        where = f" / {num(op.tank_litres)} L" if op.mix_type == "water" and op.tank_litres else ""
        if op.mix_type == "dry":
            where = f" ({ui('dry mix')})"
        label = f"{op.date:%d/%m/%y} · " + (f"{op.mix_name} · " if op.mix_name else "") + parts + where
        out.append(
            {
                "id": op.pk,
                "label": label,
                "name": op.mix_name,
                "mix_type": op.mix_type,
                "tank": float(op.tank_litres),
                "mix_dose": float(op.mix_dose),
                "packet": float(op.packet_size),
                "rounds": float(op.rounds),
                "lines": [
                    {
                        "chem": x.agri_input_id,
                        "dose": float(x.dose_qty),
                        "basis": x.dose_basis,
                        "water": float(x.dose_water),
                    }
                    for x in lines
                ],
            }
        )
        if len(out) >= limit:
            break
    # named mixes first, each group newest first
    return sorted(out, key=lambda x: not x["name"])


def _payroll_edit_data(cfg, instance):
    """Payroll edit page: payment history under the form and the figures for the live balance panel."""
    if cfg.model is not m.SalaryPayment:
        return {}
    p = instance
    data = {
        "salary_live": {
            "locked": bool(p and p.breakdown),
            "basic": float(p.basic_amount) if p else 0,
            "ot": float(p.ot_amount) if p else 0,
            "daily_rate": float(p.daily_rate) if p else 0,
            "ot_rate": float(p.ot_rate) if p else 0,
            "previous_pending": float(p.previous_pending) if p else 0,
            "paid": float(p.amount_paid) if p else 0,
            "carried": float(p.carried_amount) if p else 0,
            # which statuses mean 'paid': the current payment counts only with one of these
            "final_statuses": list(m.PaymentStatus.objects.filter(is_final=True).values_list("pk", flat=True)),
        }
    }
    if p:
        data.update(_salary_history(p))
        data["p"] = p
    return data


@login_required
def crud_delete(request, slug, pk):
    cfg = _cfg_or_404(slug)
    if denied := _edit_denied(request, cfg):
        return denied
    obj = get_object_or_404(cfg.model, pk=pk)
    plan = deletion.Plan(obj)
    if request.GET.get("preview") == "1":  # the delete pop-up: what else this delete affects
        return JsonResponse(plan.as_json())
    if request.method == "POST":
        if plan.can_delete:
            try:
                plan.execute()
                also = sum(g["count"] for (_, action), g in plan.groups.items() if action == "delete")
                text = "Deleted: {name}" + (" (and {n} linked records)" if also else "")
                messages.success(request, ui(text, name=model_label(cfg.model), n=also))
            except (ProtectedError, ValidationError) as e:
                messages.error(
                    request, ui("Could not delete: {error}", error=" ".join(getattr(e, "messages", [str(e)])))
                )
        else:
            messages.error(
                request,
                ui(
                    "{name} is still used by other records, so it can't be deleted. Mark it inactive instead.", name=obj
                ),
            )
        nxt = request.POST.get("next", "")
        # the deleted record's own pages are gone: go to the list instead
        own_page = f"/{slug}/{pk}/" in nxt or (cfg.model is m.SalaryPaid and f"/payment/{pk}/" in nxt)
        if nxt and not own_page and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
            return redirect(nxt)
        return redirect("estate:list", slug=slug)
    return render(
        request,
        "estate/confirm_delete.html",
        {"cfg": cfg, "obj": obj, "record_name": model_label(cfg.model), "impact": plan.as_json()},
    )


@login_required
def crud_export_csv(request, slug):
    cfg = _cfg_or_404(slug)
    fields = [f for f in cfg.model._meta.fields if f.name not in ("created_by", "breakdown")]
    qs, _ = apply_filters(cfg, request, with_related(cfg.model, [f.name for f in fields if f.is_relation]))
    names = [f.name for f in fields]
    rows = ([cell_value(obj, n, plain="csv") for n in names] for obj in qs.iterator(chunk_size=500))
    return csv_stream(f"{slug}-{timezone.localdate()}.csv", [field_label(cfg.model, n) for n in names], rows)


@login_required
def crud_export_pdf(request, slug):
    cfg = _cfg_or_404(slug)
    qs, _ = apply_filters(cfg, request, base_queryset(cfg))
    columns = columns_for(cfg.model, cfg.list_fields)
    count = qs.count()
    rows = [[cell_value(o, c["name"], plain="pdf") for c in columns] for o in qs[:PDF_MAX_ROWS]]
    totals = totals_for(cfg, qs)
    total_row = None
    if totals:
        total_row = [num(totals[c["name"]]) if c["name"] in totals else "" for c in columns]
        total_row[0] = total_row[0] or ui("Total")
    pdf = new_pdf(module_title(cfg), filter_summary(request), columns=len(columns))
    pdf.data_table([c["label"] for c in columns], rows, [c["numeric"] for c in columns], total_row)
    pdf.note(ui("{n} records", n=count))
    if count > len(rows):
        pdf.note(ui("showing the first {n}; export CSV for all", n=len(rows)))
    return pdf.response(f"{slug}-{timezone.localdate()}.pdf")


@login_required
def harvest_people(request):
    """For the harvest form's live preview: the people and OT hours of a date and picking job, and the
    quantity of the other harvests of that day (the browser adds the quantity being typed)."""
    day = parse_date(request.GET.get("date"))
    job_id = request.GET.get("job", "")
    if not job_id.isdigit() and request.GET.get("product", "").isdigit():
        product = m.Product.objects.filter(pk=int(request.GET["product"]))
        job_id = str(product.values_list("harvest_job_type_id", flat=True).first() or "")
    if not day or not job_id.isdigit():
        return JsonResponse({"ok": False})
    others = m.Harvest.objects.filter(date=day, job_type_id=int(job_id))
    if request.GET.get("pk", "").isdigit():
        others = others.exclude(pk=int(request.GET["pk"]))
    using = router.db_for_read(m.Harvest)  # real or demo data, whichever this session shows
    data = {k: float(v) for k, v in productivity.people(day, int(job_id), using).items()}
    return JsonResponse(
        {
            "ok": True,
            **data,
            "hours": float(productivity.day_hours()),
            "other_quantity": float(others.aggregate(q=Sum("quantity"))["q"] or 0),
            "job": str(m.JobType.objects.filter(pk=int(job_id)).first() or ""),
        }
    )


@login_required
def lookup_index(request):
    items = [{"cfg": cfg, "title": module_title(cfg), "count": cfg.model.objects.count()} for cfg in LOOKUP_MODULES]
    return render(request, "estate/lookups.html", {"items": items})


# ---------------------------------------------------------------------------
# Attendance calendar: each person's month, one coloured square per day
# ---------------------------------------------------------------------------
def _text_on(color):
    """Black or white text, whichever reads better on the status colour."""
    try:
        return "#000" if appsettings.luminance(color) > 0.4 else "#fff"
    except (ValueError, TypeError):
        return "#fff"


@login_required
def attendance_calendar(request):
    today = timezone.localdate()
    try:
        y, mo = (int(x) for x in request.GET.get("month", "").split("-")[:2])
        first = dt.date(y, mo, 1)
    except (ValueError, TypeError):
        first = today.replace(day=1)
    last = (first + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
    job_filter = request.GET.get("job_type", "")
    emp_filter = request.GET.get("employee", "")

    statuses = list(m.AttendanceStatus.objects.all())
    style = {s.pk: (s.color or "#6c757d", _text_on(s.color or "#6c757d")) for s in statuses}
    marks = {}
    for emp_id, day, status_id in m.Attendance.objects.filter(date__range=(first, last)).values_list(
        "employee_id", "date", "status_id"
    ):
        marks[(emp_id, day)] = status_id
    with_marks = {e for e, _ in marks}

    employees = m.Employee.objects.select_related("job_type").order_by("full_name")
    employees = employees.filter(
        Q(pk__in=with_marks)
        | (
            Q(is_active=True)
            & (Q(date_of_joining__isnull=True) | Q(date_of_joining__lte=last))
            & (Q(date_of_leaving__isnull=True) | Q(date_of_leaving__gte=first))
        )
    )
    choices = list(employees)  # for the drop-down (before narrowing)
    if job_filter.isdigit():
        employees = employees.filter(job_type_id=int(job_filter))
    if emp_filter.isdigit():
        employees = employees.filter(pk=int(emp_filter))

    weeks = calendar.Calendar(firstweekday=6).monthdatescalendar(first.year, first.month)
    by_id = {s.pk: s for s in statuses}
    used = set(marks.values())
    shown = [s for s in statuses if s.is_active or s.pk in used]
    people = []
    for e in employees:
        counts, paid = {}, Decimal(0)
        grid = []
        for week in weeks:
            row = []
            for day in week:
                sid = marks.get((e.pk, day)) if day.month == first.month else None
                cell = {"day": day, "in_month": day.month == first.month, "future": day > today, "status": None}
                if sid:
                    st = by_id[sid]
                    cell.update(status=st, bg=style[sid][0], fg=style[sid][1])
                    counts[sid] = counts.get(sid, 0) + 1
                    paid += st.pay_fraction or 0
                elif cell["in_month"] and not cell["future"]:
                    cell["missing"] = True
                row.append(cell)
            grid.append(row)
        missing = sum(1 for r in grid for c in r if c.get("missing"))
        marked = sum(counts.values())
        present = sum(n for sid, n in counts.items() if by_id[sid].pay_fraction)
        total = marked + missing
        people.append(
            {
                "emp": e,
                "weeks": grid,
                # every status on top of the tile (zeros too), then a small analysis of the month
                "chips": [(s, style[s.pk][0], style[s.pk][1], counts.get(s.pk, 0)) for s in shown],
                "paid": paid,
                "marked": marked,
                "present": present,
                "missing": missing,
                "rate": round(present * 100 / marked) if marked else None,
                "bar": [
                    (style[s.pk][0], counts[s.pk] * 100 / total, s, counts[s.pk])
                    for s in shown
                    if counts.get(s.pk) and total
                ]
                + ([("", missing * 100 / total, None, missing)] if missing and total else []),
            }
        )
    prev_month = (first - dt.timedelta(days=1)).replace(day=1)
    next_month = last + dt.timedelta(days=1)
    week_days = [ui(d) for d in ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")]  # weeks start on Sunday
    return render(
        request,
        "estate/attendance_calendar.html",
        {
            "first": first,
            "month_value": first.strftime("%Y-%m"),
            "month_label": f"{ui(first.strftime('%B'))} {first.year}",
            "prev_month": prev_month.strftime("%Y-%m"),
            "next_month": next_month.strftime("%Y-%m"),
            "people": people,
            "single": len(people) == 1 and emp_filter.isdigit(),
            "week_days": week_days,
            "legend": [(s, style[s.pk][0], style[s.pk][1]) for s in shown],
            "job_types": m.JobType.objects.filter(is_active=True),
            "employees": choices,
            "job_filter": job_filter,
            "emp_filter": emp_filter,
            "today": today,
        },
    )


# ---------------------------------------------------------------------------
# Daily attendance for everyone on one screen
# ---------------------------------------------------------------------------
@login_required
def attendance_bulk(request):
    day = parse_date(request.GET.get("date") or request.POST.get("date")) or timezone.localdate()
    job_filter = request.GET.get("job_type") or request.POST.get("job_type_filter") or ""
    employees = (
        m.Employee.objects.filter(is_active=True, date_of_joining__lte=day)
        .filter(Q(date_of_leaving__isnull=True) | Q(date_of_leaving__gte=day))
        .select_related("job_type")
    )
    if job_filter.isdigit():
        employees = employees.filter(job_type_id=int(job_filter))
    statuses = list(m.AttendanceStatus.objects.filter(is_active=True))
    status_ids = {s.pk for s in statuses}
    existing = {a.employee_id: a for a in m.Attendance.objects.filter(date=day, employee__in=employees)}

    if request.method == "POST":
        block_id = request.POST.get("block", "")
        by_id = {s.pk: s for s in statuses}
        fields = ["status", "ot_hours", "remarks"] + (["block"] if block_id.isdigit() else [])
        saved = 0
        with transaction.atomic(), productivity.paused():  # harvests of the day re-worked once
            for emp in employees:
                raw = request.POST.get(f"status_{emp.pk}", "")
                if not raw.isdigit() or int(raw) not in status_ids:
                    continue
                try:
                    ot = max(Decimal(request.POST.get(f"ot_{emp.pk}") or 0), Decimal(0))
                except InvalidOperation:
                    ot = Decimal(0)
                values = {
                    "status": by_id[int(raw)],
                    "ot_hours": ot,
                    "remarks": request.POST.get(f"remarks_{emp.pk}", "")[:200],
                }
                if block_id.isdigit():
                    values["block_id"] = int(block_id)
                saved += 1
                att = existing.get(emp.pk)
                if att is None:
                    att = m.Attendance(employee=emp, date=day, created_by=request.user, **values)
                    try:
                        with transaction.atomic():
                            att.save(force_insert=True)
                    except IntegrityError:  # saved by someone else a moment ago
                        m.Attendance.objects.update_or_create(employee=emp, date=day, defaults=values)
                    continue
                if (att.status_id, att.ot_hours, att.remarks, att.block_id) == (
                    values["status"].pk,
                    ot,
                    values["remarks"],
                    values.get("block_id", att.block_id),
                ):
                    continue  # nothing changed: no write
                for key, value in values.items():
                    setattr(att, key, value)
                att.employee = emp  # already loaded (for the activity log text)
                att.save(update_fields=[*fields, "updated_at"])
        messages.success(request, ui("Attendance saved for {n} employees on {date}.", n=saved, date=dates.fmt(day)))
        return redirect(f"{reverse('estate:attendance_bulk')}?date={day.isoformat()}&job_type={job_filter}")

    counts = {}
    for a in existing.values():
        counts[a.status_id] = counts.get(a.status_id, 0) + 1
    return render(
        request,
        "estate/attendance_bulk.html",
        {
            "day": day,
            "rows": [{"emp": e, "att": existing.get(e.pk)} for e in employees],
            "statuses": statuses,
            "summary": [(s, counts.get(s.pk, 0)) for s in statuses],
            "job_types": m.JobType.objects.filter(is_active=True),
            "blocks": m.Block.objects.filter(is_active=True),
            "job_filter": job_filter,
            "prev_day": day - dt.timedelta(days=1),
            "next_day": day + dt.timedelta(days=1),
        },
    )


# ---------------------------------------------------------------------------
# Payroll
# ---------------------------------------------------------------------------
@login_required
def payroll_generate(request):
    today = timezone.localdate()
    initial = {"date_from": today.replace(day=1), "date_to": today}
    if request.GET.get("employee", "").isdigit():  # opened from an employee's page
        initial["employee"] = [int(request.GET["employee"])]
    if request.GET.get("gang_leader", "").isdigit():
        initial["gang_leader"] = [request.GET["gang_leader"]]
    form = PayrollGenerateForm(request.POST or None, initial=initial)
    preview = []
    if request.method == "POST" and form.is_valid():
        d1, d2 = form.cleaned_data["date_from"], form.cleaned_data["date_to"]
        job_type = form.cleaned_data["job_type"]
        commit = "confirm" in request.POST
        status = payroll.default_status()
        if commit and status is None:
            messages.error(
                request,
                ui("Add an active payment status that is not final (e.g. Pending) in the drop-down lists first."),
            )
            commit = False

        blocks = list(form.cleaned_data["blocks"])
        block_ids = {b.pk for b in blocks}  # empty: all plots

        def row_status(existing):
            """(state, payroll to re-use, why it is skipped). With "Re-calculate existing unpaid payroll"
            ticked, unpaid payrolls overlapping the period and plots are replaced by the new calculation
            (one is re-used, the others deleted) - also when their dates or plots differ."""
            if not existing:
                return "new", None, ""
            if any(p.is_locked or p.payments.exists() for p in existing):
                return "exists", None, "paid"  # money paid / entered or balance moved on: never re-calculated
            if not form.cleaned_data["overwrite"]:
                return "exists", None, "tick"
            same = next(
                (p for p in existing if (p.period_from, p.period_to) == (d1, d2) and p.plot_ids == block_ids),
                existing[0],
            )
            return "update", same, ""

        # unpaid balances of earlier payrolls, per payee: added as "previous pending" when ticked
        pending = {}
        if form.cleaned_data["include_pending"]:
            for old in (
                m.SalaryPayment.objects.filter(pay_state__in=("unpaid", "part"), balance__gt=0, period_to__lt=d1)
                .select_related("status")
                .prefetch_related("blocks")
                .order_by("period_to", "id")
            ):
                # a payroll for some plots only takes over balances of the same plots (not other plots',
                # nor all-plots ones); a payroll for all plots takes over every earlier balance
                if block_ids and not (old.plot_ids and old.plot_ids <= block_ids):
                    continue
                key = ("e", old.employee_id) if old.employee_id else ("g", old.gang_leader_id)
                pending.setdefault(key, []).append(old)

        rows = []  # (calc, employee, gang leader id, existing payments)
        # "Paid to": ticked employees and / or gang leaders; nothing ticked = everybody
        picked_emps = form.cleaned_data["employee"]
        picked_leaders = {int(x) for x in form.cleaned_data["gang_leader"]}
        picking_people = bool(picked_emps or picked_leaders)
        if form.cleaned_data["include_employees"] and (picked_emps or not picking_people):
            # everyone with attendance or individual work in the period (also those registered later than
            # their first work day, and those who left during it)
            att_qs = m.Attendance.objects.filter(date__range=(d1, d2))
            work_qs = m.WorkEntry.objects.filter(date__range=(d1, d2))
            if block_ids:  # who worked on the plots (calculate() keeps only those days)
                att_qs, work_qs = att_qs.filter(block_id__in=block_ids), work_qs.filter(block_id__in=block_ids)
            worked = set(att_qs.values_list("employee_id", flat=True)) | set(
                work_qs.values_list("employee_id", flat=True)
            )
            emps = m.Employee.objects.filter(pk__in=worked).select_related("job_type")
            if picked_emps:  # only the chosen employees' payroll for the period
                emps = emps.filter(pk__in=[e.pk for e in picked_emps])
            if form.cleaned_data["employment_type"]:
                emps = emps.filter(employment_type=form.cleaned_data["employment_type"])
            if job_type:
                emps = emps.filter(job_type=job_type)
            ctx = payroll.PayrollContext(d1, d2, employees=emps)
            existing_by_emp = {}
            for p in (
                m.SalaryPayment.objects.filter(employee__in=emps, period_from__lte=d2, period_to__gte=d1)
                .select_related("status")
                .prefetch_related("blocks")
                .order_by("id")
            ):
                if payroll.plots_overlap(p.plot_ids, block_ids):  # payrolls for other plots don't count
                    existing_by_emp.setdefault(p.employee_id, []).append(p)
            for emp in emps:
                calc = payroll.calculate(emp, d1, d2, ctx, block_ids=block_ids)
                if calc:
                    rows.append((calc, emp, None, existing_by_emp.get(emp.pk, [])))
        # day labourers (paid per gang leader) have no employee type, so they are left out when one is chosen
        with_counts = form.cleaned_data["include_counts"] and not form.cleaned_data["employment_type"]
        if with_counts and (picked_leaders or not picking_people):
            leaders = {g.pk: g for g in m.GangLeader.objects.all()}
            for leader_id, calc in payroll.calculate_counts(d1, d2, job_type, block_ids=block_ids).items():
                if picked_leaders and (leader_id or 0) not in picked_leaders:
                    continue
                existing = payroll.existing_count_payments(d1, d2, leader_id, block_ids)
                rows.append((calc, None, leaders.get(leader_id), existing))

        # the preview's tick boxes: only the ticked rows are created (all of them when none were shown yet)
        picked = set(request.POST.getlist("pick")) if "picking" in request.POST else None
        created = updated = skipped = 0
        with transaction.atomic():
            for calc, emp, leader, existing in rows:
                state, same_period, why = row_status(existing)
                key = f"e{emp.pk}" if emp else f"g{leader.pk if leader else 0}"
                earlier = pending.get(("e", emp.pk) if emp else ("g", leader.pk if leader else None), [])
                if state == "exists":
                    earlier = []
                earlier_total = sum((o.balance for o in earlier), Decimal(0))
                preview.append(
                    {
                        **calc,
                        "pending": earlier_total,
                        "pending_from": earlier,
                        "payable": calc["net"] + earlier_total,
                        "emp": emp,
                        "leader": leader,
                        "is_count": emp is None,
                        "status": state,
                        "why": why,
                        "replaces": len(existing) if state == "update" else 0,
                        "key": key,
                        "existing": existing,  # links to the payroll already made for the period
                        "labels": [
                            payroll.source_label(x["source"]) + (f" · {x['job']}" if x.get("job") else "")
                            for x in calc["lines"]
                        ],
                    }
                )
                if not commit:
                    continue
                if state == "exists" or (picked is not None and key not in picked):
                    skipped += 1
                    continue
                if state == "update":
                    # the other overlapping unpaid payrolls are replaced by this one; balances that had been
                    # moved into them become pending again where they came from, and are taken over below
                    for extra in existing:
                        if extra.pk == same_period.pk:
                            continue
                        returned = list(extra.carried_from.all())
                        extra.delete()
                        if form.cleaned_data["include_pending"]:
                            for old in returned:
                                old.refresh_from_db()
                                if old.balance > 0 and old not in earlier:
                                    earlier = [*earlier, old]
                    earlier_total = sum((o.balance for o in earlier), Decimal(0))
                saved = payroll.save(
                    emp,
                    d1,
                    d2,
                    calc,
                    user=request.user,
                    status=status,
                    gang_leader=leader,
                    blocks=blocks,
                    payment=same_period if state == "update" else None,
                )
                if earlier:  # move the earlier balances into this payroll; they are paid here now
                    for old in earlier:
                        old.carry_into(saved)
                    saved.previous_pending = (saved.previous_pending or 0) + earlier_total
                    saved.save()
                if state == "update":
                    updated += 1
                else:
                    created += 1
        if commit:
            msg = N_(
                "Payroll generated: {created} created, {updated} re-calculated, {skipped} skipped (already exist, paid or not ticked)."  # noqa: E501
            )
            messages.success(request, ui(msg, created=created, updated=updated, skipped=skipped))
            return redirect(f"{reverse('estate:list', args=['salary-payments'])}?date_from={d1}&date_to={d2}")
    return render(
        request,
        "estate/payroll_generate.html",
        {
            "form": form,
            "preview": preview,
            "preview_total": sum((r["payable"] for r in preview if r["status"] != "exists"), Decimal(0)),
            "preview_pending": sum((r["pending"] for r in preview), Decimal(0)),
            "counts": {k: sum(1 for r in preview if r["status"] == k) for k in ("new", "update", "exists")},
            "plots": list(form.cleaned_data.get("blocks") or []) if form.is_bound and form.is_valid() else [],
        },
    )


def _payslip_lines(p):
    return [
        {**line, "label": payroll.source_label(line["source"]) + (f" · {line['job']}" if line.get("job") else "")}
        for line in p.breakdown or []
    ]


def _payslip_obj(pk):
    return get_object_or_404(
        m.SalaryPayment.objects.select_related(
            "employee__job_type", "employee__employment_type", "gang_leader", "status", "payment_mode"
        ),
        pk=pk,
    )


@login_required
def payslip(request, pk):
    p = _payslip_obj(pk)
    cfg = get_cfg("salary-payments")
    return render(
        request,
        "estate/payslip.html",
        {
            "p": p,
            "lines": _payslip_lines(p),
            **_salary_history(p),
            "can_pay": cfg.can_edit(request.user) and p.balance > 0 and not p.carried_to_id,
            "today": timezone.localdate(),
            "modes": m.PaymentMode.objects.filter(is_active=True),
        },
    )


def _salary_history(p, current=None):
    """Everything paid against a payroll, in order, with the balance after each payment, plus where its
    pending balance came from (earlier payrolls) and where it went (a later payroll)."""
    payments = list(p.payments.select_related("payment_mode").order_by("date", "id"))
    return {
        "payments": payments,
        "pending_entries": sum((x.amount for x in payments if not x.is_paid), Decimal(0)),
        "current": current,
        "carried_from": list(p.carried_from.select_related("employee", "gang_leader").order_by("period_to")),
    }


@login_required
def salary_paid_detail(request, pk):
    """One payment made, with the whole payment history of its payroll (each entry links to its own page)."""
    pay = get_object_or_404(
        m.SalaryPaid.objects.select_related(
            "salary__employee", "salary__gang_leader", "salary__status", "payment_mode"
        ),
        pk=pk,
    )
    p = pay.salary
    return render(
        request,
        "estate/salary_paid.html",
        {
            "pay": pay,
            "p": p,
            **_salary_history(p, current=pay),
            "can_edit": get_cfg("salary-paid").can_edit(request.user),
        },
    )


@login_required
def payslip_pdf(request, pk):
    p = _payslip_obj(pk)
    sp = m.SalaryPayment
    pdf = new_pdf(ui("Salary slip"), f"{dates.fmt(p.period_from)} – {dates.fmt(p.period_to)}")
    if p.is_labour_count:
        jobs = ", ".join(dict.fromkeys(x["job"] for x in p.breakdown or [] if x.get("job")))
        pdf.kpis(
            [
                (field_label(sp, "gang_leader"), p.payee_name),
                (ui("Daily labour count"), jobs),
                (ui("Plots"), p.plots_text or ui("All plots")),
                (field_label(sp, "net_amount"), inr(p.net_amount)),
            ]
        )
        rows = [[ui("Man-days (workers × days)"), num(p.days_worked)]]
    else:
        pdf.kpis(
            [
                (field_label(sp, "employee"), f"{p.employee.full_name} ({p.employee.employee_code})"),
                (
                    field_label(m.Employee, "job_type"),
                    str(p.employee.job_type) + (f" · {p.block}" if p.block_id else ""),
                ),
                (field_label(sp, "net_amount"), inr(p.net_amount)),
            ]
        )
        rows = [
            [
                ui("Days worked ({full} full + {half} half)", full=num(p.full_days), half=num(p.half_days)),
                num(p.days_worked),
            ]
        ]
    rows += [[f"{num(x['days'])} × {inr(x['rate'])} · {x['label']}", inr(x["basic"])] for x in _payslip_lines(p)]
    rows += [
        [field_label(sp, "basic_amount"), inr(p.basic_amount)],
        [f"{field_label(sp, 'ot_amount')} ({num(p.ot_hours)} h)", inr(p.ot_amount)],
        [field_label(sp, "bonus"), inr(p.bonus)],
        [field_label(sp, "advance_deduction"), "- " + inr(p.advance_deduction)],
        [field_label(sp, "other_deduction"), "- " + inr(p.other_deduction)],
    ]
    if p.previous_pending:
        rows.append([field_label(sp, "previous_pending"), inr(p.previous_pending)])
    pdf.data_table(
        [ui("Earnings & deductions"), ui("Amount")],
        rows,
        [False, True],
        [field_label(sp, "net_amount"), inr(p.net_amount)],
    )
    if p.previous_pending:
        pdf.note(f"{field_label(sp, 'previous_pending')}: {inr(p.previous_pending)}")
    payments = list(p.payments.select_related("payment_mode").order_by("date", "id"))
    if payments:
        pdf.section(ui("Payments"))
        pdf.data_table(
            [ui("Date"), ui("Amount"), ui("Payment status"), ui("Payment mode"), ui("Balance after this payment")],
            [
                [
                    dates.fmt(x.date),
                    inr(x.amount),
                    ui(str(x.get_status_display())),
                    str(x.payment_mode or ""),
                    inr(x.balance_after),
                ]
                for x in payments
            ],
            [False, True, False, False, True],
        )
    state = ui(str(p.get_pay_state_display()))
    pdf.note(f"{state} · {ui('Paid')}: {inr(p.amount_paid)} · {ui('Balance (pending)')}: {inr(p.balance)}")
    pdf.ln(18)
    pdf.cell(90, 6, ui("Employer signature"))
    pdf.cell(0, 6, ui("Gang leader signature") if p.is_labour_count else ui("Employee signature"), align="R")
    who = p.employee.employee_code if p.employee_id else f"count-{p.gang_leader_id or 0}"
    return pdf.response(f"payslip-{who}-{p.period_to}.pdf")


@login_required
def employee_profile(request, pk):
    emp = get_object_or_404(m.Employee.objects.select_related("job_type", "employment_type", "gender"), pk=pk)
    today = timezone.localdate()
    year_start = today.replace(month=1, day=1)
    att = m.Attendance.objects.filter(employee=emp, date__gte=year_start)
    by_status = dict(att.values_list("status_id").annotate(n=Count("id")))
    wage_today, ot_today = emp.wages_on(today)
    return render(
        request,
        "estate/employee_profile.html",
        {
            "emp": emp,
            "summary": [(s, by_status.get(s.pk, 0)) for s in m.AttendanceStatus.objects.all()],
            "ot": att.aggregate(s=Sum("ot_hours"))["s"] or 0,
            "pays": m.SalaryPayment.objects.filter(employee=emp).select_related("status")[:12],
            "paid_total": m.SalaryPayment.objects.filter(employee=emp, period_to__gte=year_start).aggregate(
                s=Sum("net_amount")
            )["s"]
            or 0,
            "works": m.WorkEntry.objects.filter(employee=emp).select_related("job_type", "block")[:15],
            "recent_att": att.select_related("job_type", "status")[:31],
            "year": today.year,
            "wage_today": wage_today,
            "ot_today": ot_today,
        },
    )
