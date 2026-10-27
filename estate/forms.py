import datetime as dt
from itertools import pairwise

from django import forms
from django.db.models import Q
from django.forms import BaseInlineFormSet, inlineformset_factory, modelform_factory

from core import dates
from core.i18n import N_, field_help, field_label, ui

from . import models as m


class DateInput(forms.DateInput):
    """Text box showing DD/MM/YY with a calendar pop-up (static/js/dates.js); typing also works."""

    input_type = "text"

    def __init__(self, attrs=None, **kw):
        attrs = {"class": "pms-date", "placeholder": dates.PLACEHOLDER, "autocomplete": "off", **(attrs or {})}
        super().__init__(attrs=attrs, format=dates.DISPLAY, **kw)


class DMYDateField(forms.DateField):
    """Understands 13/10/26, 13/10/2026, 2026-10-13 ... (see core/dates.py)."""

    def to_python(self, value):
        if value in self.empty_values:
            return None
        day = dates.parse(value)
        if day is None:
            raise forms.ValidationError(
                ui("Enter a date as DD/MM/YY, e.g. {example}.", example=dates.fmt(dt.date.today())), code="invalid"
            )
        return day


def style_fields(fields, small=False):
    """Bootstrap classes and mobile-friendly keyboards for every widget."""
    sm = " form-control-sm" if small else ""
    for field in fields.values():
        if isinstance(field, forms.DateField) and not isinstance(field, DMYDateField):
            field.__class__ = DMYDateField  # one date format everywhere
            if not isinstance(field.widget, DateInput):
                field.widget = DateInput()
        w = field.widget
        if isinstance(w, forms.CheckboxSelectMultiple):  # shown as toggle chips (templates)
            w.attrs.setdefault("class", "btn-check")
            continue
        if isinstance(w, forms.CheckboxInput):
            w.attrs.setdefault("class", "form-check-input")
        elif isinstance(w, forms.Select):
            w.attrs.setdefault("class", "form-select" + (" form-select-sm" if small else ""))
        else:
            cls = w.attrs.get("class", "")
            if "form-control" not in cls.split():
                w.attrs["class"] = f"form-control{sm} {cls}".strip()
        if isinstance(w, forms.Textarea):
            w.attrs["rows"] = 2
        if isinstance(field, forms.DecimalField):
            w.attrs.setdefault("inputmode", "decimal")
            w.attrs.setdefault("step", "any")
        elif isinstance(field, forms.IntegerField):
            w.attrs.setdefault("inputmode", "numeric")


class StyledModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        model = self._meta.model
        model_fields = {f.name for f in model._meta.get_fields()}
        for name, field in self.fields.items():
            if name not in model_fields:  # extra form-only fields label themselves
                continue
            field.label = field_label(model, name)
            field.help_text = field_help(model, name)
            qs = getattr(field, "queryset", None)
            if qs is None:
                continue
            field.empty_label = "— " + ui("choose") + " —"
            if isinstance(field, forms.ModelMultipleChoiceField):  # e.g. a payroll's plots: tick chips
                field.widget = forms.CheckboxSelectMultiple()
                field.empty_label = None  # no "— choose —" chip
                if hasattr(qs.model, "is_active"):
                    chosen = list(getattr(self.instance, name).values_list("pk", flat=True)) if self.instance.pk else []
                    qs = qs.filter(Q(is_active=True) | Q(pk__in=chosen))
                field.queryset = qs  # also gives the new widget its choices
                continue
            if hasattr(qs.model, "is_active"):
                # only active entries, but keep the current value of an old record
                current = getattr(self.instance, f"{name}_id", None)
                field.queryset = qs.filter(Q(is_active=True) | Q(pk=current))
            if model is m.SalaryPaid and name == "salary":  # only payrolls with something left to pay
                current = getattr(self.instance, "salary_id", None) or self.initial.get("salary")
                field.queryset = field.queryset.filter(Q(pay_state__in=("unpaid", "part")) | Q(pk=current))
            if model is m.SaleReceipt and name == "sale":  # only sales with something still to receive
                current = getattr(self.instance, "sale_id", None) or self.initial.get("sale")
                field.queryset = field.queryset.filter(Q(receipt_state__in=("pending", "part")) | Q(pk=current))
            related = getattr(qs.model, "STR_RELATED", ())
            if related:  # the option texts need them: one query instead of one per option
                field.queryset = field.queryset.select_related(*related)
            if field.required and not self.instance.pk and name not in self.initial and issubclass(qs.model, m.Lookup):
                first = qs.model.first_active()
                if first:
                    self.initial[name] = first.pk
        style_fields(self.fields)


def build_form_class(cfg):
    date_fields = [f.name for f in cfg.model._meta.fields if f.get_internal_type() == "DateField" and f.editable]
    return modelform_factory(
        cfg.model, form=StyledModelForm, exclude=["created_by"], widgets={name: DateInput() for name in date_fields}
    )


class DateRangeForm(forms.Form):
    date_from = forms.DateField(widget=DateInput())
    date_to = forms.DateField(widget=DateInput())

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["date_from"].label = ui("From")
        self.fields["date_to"].label = ui("To")
        style_fields(self.fields)

    def clean(self):
        d = super().clean()
        if d.get("date_from") and d.get("date_to") and d["date_to"] < d["date_from"]:
            raise forms.ValidationError(ui("'To' date must be after 'From' date."))
        return d


class PayrollGenerateForm(DateRangeForm):
    employee = forms.ModelMultipleChoiceField(
        queryset=m.Employee.objects.order_by("full_name"), required=False, widget=forms.CheckboxSelectMultiple
    )
    gang_leader = forms.MultipleChoiceField(required=False, widget=forms.CheckboxSelectMultiple)
    blocks = forms.ModelMultipleChoiceField(
        queryset=m.Block.objects.filter(is_active=True).order_by("name"),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    employment_type = forms.ModelChoiceField(queryset=m.EmploymentType.objects.filter(is_active=True), required=False)
    job_type = forms.ModelChoiceField(queryset=m.JobType.objects.filter(is_active=True), required=False)
    include_employees = forms.BooleanField(required=False, initial=True)
    include_counts = forms.BooleanField(required=False, initial=True)
    overwrite = forms.BooleanField(required=False)
    include_pending = forms.BooleanField(required=False, initial=True)

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["employee"].label = ui("Paid to")
        self.fields["employee"].help_text = ui(
            "Employees and / or gang leaders (their Daily Labour Count). None ticked: everybody."
        )
        # gang leaders: the Daily Labour Count paid to them ("0" = counts entered without a gang leader)
        self.fields["gang_leader"].choices = [(str(g.pk), str(g)) for g in m.GangLeader.objects.order_by("name")] + [
            ("0", ui("Daily labour count (no gang leader)"))
        ]
        self.fields["employment_type"].label = ui("Employee type")
        self.fields["job_type"].label = ui("Job / work type")
        self.fields["include_employees"].label = ui("Registered employees (attendance & individual work)")
        self.fields["include_counts"].label = ui("Daily Labour Count workers (paid per gang leader)")
        self.fields["overwrite"].label = ui("Re-calculate existing unpaid payroll for this period")
        self.fields["include_pending"].label = ui("Add earlier unpaid balances (previous pending) to this payroll")
        self.fields["blocks"].label = ui("Plots")
        self.fields["blocks"].help_text = ui(
            "Only the work on the ticked plots: one payroll per employee / gang leader. None ticked: all plots."
        )
        for name in ("employment_type", "job_type"):
            self.fields[name].empty_label = ui("All")
        style_fields(self.fields)
        self.fields["employee"].widget.attrs = {}  # rendered by the template as a searchable check list
        self.fields["gang_leader"].widget.attrs = {}
        self.fields["blocks"].widget.attrs = {}


# ---------------------------------------------------------------------------
# Wage history rows inside the Job Type form
# ---------------------------------------------------------------------------
WAGE_HISTORY_FIELDS = [
    "effective_from",
    "effective_to",
    "rate",
    "ot_rate_per_hour",
    "workers",
    "block",
    "employee",
    "employment_type",
    "remarks",
]


def limit_wage_scope_choices(form):
    """Wage rate forms: active plots and employees only (but keep an old record's own choice)."""
    for name, model in (("block", m.Block), ("employee", m.Employee)):
        field = form.fields.get(name)
        if field is None or not hasattr(model, "is_active"):
            continue
        current = getattr(form.instance, f"{name}_id", None)
        field.queryset = model.objects.filter(Q(is_active=True) | Q(pk=current))


class WageHistoryFormSet(BaseInlineFormSet):
    def _construct_form(self, i, **kwargs):
        form = super()._construct_form(i, **kwargs)
        form.instance._skip_overlap_check = True  # rows are checked together in clean()
        for name, field in form.fields.items():
            if name in WAGE_HISTORY_FIELDS:
                field.label = field_label(m.WageRate, name)
        if form.instance.pk is None:  # blank new rows
            form.initial = {}
            for name in ("effective_from", "rate", "ot_rate_per_hour"):
                form.fields[name].initial = None
        form.fields["ot_rate_per_hour"].required = False
        form.fields["ot_rate_per_hour"].widget.attrs["placeholder"] = ui("default")
        form.fields["employment_type"].empty_label = ui("All employee types")
        form.fields["block"].empty_label = ui("All plots")
        form.fields["employee"].empty_label = ui("Everybody")
        limit_wage_scope_choices(form)
        style_fields(form.fields, small=True)
        return form

    def clean(self):
        super().clean()
        rows = []
        for f in self.forms:
            d = getattr(f, "cleaned_data", None)
            if not d or d.get("DELETE") or not d.get("effective_from"):
                continue
            scope = tuple(
                (d.get(name).pk if d.get(name) else 0) for name in ("block", "employee", "employment_type")
            ) + (d.get("workers") or "all",)
            rows.append((scope, d["effective_from"], d.get("effective_to")))
        rows.sort(key=lambda r: (r[0], r[1]))
        for a, b in pairwise(rows):
            if a[0] != b[0]:
                continue
            if a[1] == b[1]:
                raise forms.ValidationError(
                    ui("Two wage periods start on the same date ({date}).", date=f"{a[1]:%d/%m/%y}")
                )
            if a[2] is not None and a[2] >= b[1]:
                msg = N_("Wage periods overlap: one runs until {end} but the next starts {start}. Fix the earlier row.")
                raise forms.ValidationError(ui(msg, end=f"{a[2]:%d/%m/%y}", start=f"{b[1]:%d/%m/%y}"))


def build_wage_history_formset(extra=1):
    return inlineformset_factory(
        m.JobType,
        m.WageRate,
        formset=WageHistoryFormSet,
        fields=WAGE_HISTORY_FIELDS,
        extra=extra,
        can_delete=True,
        widgets={"effective_from": DateInput(), "effective_to": DateInput()},
    )


# ---------------------------------------------------------------------------
# Fertilizers / chemicals: purchases (on the chemical's page) and the chemicals used in field work
# ---------------------------------------------------------------------------
NUMBER_FIELDS_ZERO = ("bags", "pack_size", "price_per_bag", "dose_qty", "dose_water", "quantity", "unit_cost")


class _ZeroIfBlank:
    """Number boxes may be left empty: empty means 0 (worked out when saving where it can be)."""

    def _post_clean(self):
        for name in NUMBER_FIELDS_ZERO:
            if name in self.cleaned_data and self.cleaned_data[name] is None:
                self.cleaned_data[name] = 0
        super()._post_clean()


class PurchaseForm(_ZeroIfBlank, StyledModelForm):
    class Meta:
        model = m.AgriInputPurchase
        fields = ["date", "bags", "pack_size", "price_per_bag", "supplier", "invoice_no"]
        widgets = {"date": DateInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("bags", "pack_size", "price_per_bag"):
            self.fields[name].required = False
        style_fields(self.fields, small=True)


class ChemicalLineForm(_ZeroIfBlank, StyledModelForm):
    class Meta:
        model = m.FieldOperationInput
        fields = ["agri_input", "dose_qty", "dose_basis", "dose_water", "purchase", "quantity", "unit_cost"]
        # dose_water: the jar size the amount is for (kept in step with the entry's jar size by the page)
        widgets = {
            "agri_input": forms.HiddenInput(),
            "dose_water": forms.HiddenInput(),
            "dose_basis": forms.HiddenInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("dose_qty", "dose_water", "quantity", "unit_cost"):
            self.fields[name].required = False
        self.fields["agri_input"].queryset = m.AgriInput.objects.all()  # an old entry may use an inactive one
        self.fields["purchase"].queryset = m.AgriInputPurchase.objects.select_related("agri_input")
        self.fields["purchase"].empty_label = "— " + ui("no purchase (enter the cost)") + " —"
        style_fields(self.fields, small=True)

    def clean(self):
        data = super().clean()
        purchase, chem = data.get("purchase"), data.get("agri_input")
        if purchase and chem and purchase.agri_input_id != chem.pk:
            self.add_error("purchase", ui("This purchase is of another fertilizer / chemical."))
        return data


def build_purchase_formset():
    return inlineformset_factory(
        m.AgriInput, m.AgriInputPurchase, form=PurchaseForm, fields=PurchaseForm.Meta.fields, extra=0, can_delete=True
    )


def build_chemical_formset():
    return inlineformset_factory(
        m.FieldOperation,
        m.FieldOperationInput,
        form=ChemicalLineForm,
        fields=ChemicalLineForm.Meta.fields,
        extra=0,
        can_delete=True,
    )
