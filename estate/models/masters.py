import datetime as dt
import re
from itertools import pairwise

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core import appsettings

from .base import POSITIVE, LocalizedNameMixin, TimeStampedModel, money_field
from .lookups import CropRole, EmploymentType, Gender, InputType, JobCategory, Unit


class JobType(LocalizedNameMixin, TimeStampedModel):
    name = models.CharField("Job type", max_length=80, unique=True)
    category = models.ForeignKey(JobCategory, verbose_name="Category", null=True, blank=True, on_delete=models.SET_NULL)
    default_daily_wage = money_field(
        "Default daily wage", help_text="Used only for dates not covered by the wage history below"
    )
    default_ot_rate = money_field(
        "Default overtime rate / hour", help_text="Used only for dates not covered by the wage history below"
    )
    description = models.TextField("Description", blank=True)
    is_active = models.BooleanField("Active", default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Job type"
        verbose_name_plural = "Job types"

    def rate_on(self, on_date, ctx=None):
        return wage_rate_on(self.pk, on_date, ctx)

    def wages_on(self, on_date, ctx=None):
        """(daily wage, OT rate / hour) on a date for some work (see WorkContext): wage history first, else
        the defaults."""
        return self.wages_from(self.rate_on(on_date, ctx))

    def wages_from(self, rate):
        if rate:
            return rate.rate, (rate.ot_rate_per_hour or self.default_ot_rate)
        return self.default_daily_wage, self.default_ot_rate


class Product(LocalizedNameMixin, TimeStampedModel):
    name = models.CharField("Product / Crop", max_length=80, unique=True)
    crop_role = models.ForeignKey(CropRole, verbose_name="Crop role", null=True, blank=True, on_delete=models.SET_NULL)
    unit = models.ForeignKey(Unit, verbose_name="Unit", null=True, blank=True, on_delete=models.SET_NULL)
    raw_form = models.CharField(
        "Raw form name", max_length=60, blank=True, help_text="e.g. Green capsules, Green berries, Fresh cherry"
    )
    processed_form = models.CharField(
        "Processed form name", max_length=60, blank=True, help_text="e.g. Dry cardamom, Black pepper, Dry cherry"
    )
    typical_recovery_percent = models.DecimalField(
        "Usual recovery %",
        max_digits=5,
        decimal_places=2,
        default=0,
        validators=POSITIVE,
        help_text="Processed output as % of raw input (shown as a guide)",
    )
    typical_rate = money_field("Usual sale rate per unit", help_text="Filled in on new sales; can be changed there")
    harvest_months = models.CharField(
        "Harvest months",
        max_length=40,
        blank=True,
        help_text="Month numbers separated by commas, e.g. 8,9,10,11,12,1,2",
    )
    harvest_job_type = models.ForeignKey(
        JobType,
        verbose_name="Picking / harvesting job",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    is_active = models.BooleanField("Active", default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Product / Crop"
        verbose_name_plural = "Products / Crops"

    @property
    def harvest_month_list(self):
        out = []
        for part in (self.harvest_months or "").split(","):
            part = part.strip()
            if part.isdigit() and 1 <= int(part) <= 12:
                out.append(int(part))
        return out


class Block(TimeStampedModel):
    """A field / plot / division of the estate - lets you see yield and cost per block."""

    name = models.CharField("Block / Plot name", max_length=80, unique=True)
    area_acres = models.DecimalField("Area (acres)", max_digits=8, decimal_places=2, default=0, validators=POSITIVE)
    main_crop = models.ForeignKey(
        Product, verbose_name="Main crop", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    plant_count = models.PositiveIntegerField("No. of plants / clumps", default=0)
    variety = models.CharField("Variety", max_length=80, blank=True, help_text="e.g. Njallani Green Gold, Malabar")
    year_planted = models.PositiveIntegerField("Year planted", null=True, blank=True)
    irrigated = models.BooleanField("Irrigated", default=False)
    notes = models.TextField("Notes", blank=True)
    is_active = models.BooleanField("Active", default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Block / Plot"
        verbose_name_plural = "Blocks / Plots"

    def __str__(self):
        return self.name


class AgriInput(TimeStampedModel):
    """Fertilizers, pesticides, fungicides, manures... with unit and cost."""

    STR_RELATED = ("unit",)  # used by __str__: lists load it in the same query
    name = models.CharField("Name", max_length=100, unique=True)
    input_type = models.ForeignKey(InputType, verbose_name="Type", null=True, blank=True, on_delete=models.SET_NULL)
    brand = models.CharField("Brand / Manufacturer", max_length=100, blank=True)
    composition = models.CharField(
        "Composition / Active ingredient", max_length=150, blank=True, help_text="e.g. NPK 18:18:18, Spinosad 45% SC"
    )
    unit = models.ForeignKey(Unit, verbose_name="Unit", null=True, blank=True, on_delete=models.SET_NULL)
    unit_cost = money_field("Cost per unit")
    is_active = models.BooleanField("Active", default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Fertilizer / Chemical"
        verbose_name_plural = "Fertilizers / Chemicals"

    def __str__(self):
        return f"{self.name} ({self.unit})" if self.unit_id else self.name


class GangLeader(TimeStampedModel):
    """A gang leader / labour contractor (kangani) who brings day labourers and receives their pay.
    Their workers are not registered: they are entered as a count in the Daily Labour Count."""

    name = models.CharField("Name", max_length=120, unique=True)
    phone = models.CharField("Phone", max_length=20, blank=True)
    address = models.TextField("Address / Native place", blank=True)
    notes = models.TextField("Notes", blank=True)
    is_active = models.BooleanField("Active", default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Gang leader / Contractor"
        verbose_name_plural = "Gang leaders / Contractors"

    def __str__(self):
        return self.name


class Employee(TimeStampedModel):
    """Module 1 - Registration. Wages always come from the job type."""

    employee_code = models.CharField(
        "Employee code", max_length=20, unique=True, blank=True, help_text="Leave blank to number automatically"
    )
    full_name = models.CharField("Full name", max_length=120)
    gender = models.ForeignKey(Gender, verbose_name="Gender", null=True, blank=True, on_delete=models.SET_NULL)
    phone = models.CharField("Phone", max_length=20, blank=True)
    address = models.TextField("Address / Native place", blank=True)
    job_type = models.ForeignKey(JobType, verbose_name="Job type", on_delete=models.PROTECT, related_name="employees")
    employment_type = models.ForeignKey(
        EmploymentType,
        verbose_name="Employment type",
        on_delete=models.PROTECT,
        help_text="Wage and OT rate come from the job type (its wage history)",
    )
    date_of_joining = models.DateField("Date of joining", default=timezone.localdate)
    date_of_leaving = models.DateField("Date of leaving", null=True, blank=True)
    emergency_contact = models.CharField("Emergency contact", max_length=120, blank=True)
    is_active = models.BooleanField("Active (currently working)", default=True)
    notes = models.TextField("Notes", blank=True)

    class Meta:
        ordering = ["full_name"]
        verbose_name = "Employee"
        verbose_name_plural = "Employees"

    def __str__(self):
        return f"{self.full_name} ({self.employee_code})"

    def work_context(self, block_id=None):
        return WorkContext(employee_id=self.pk, employment_type_id=self.employment_type_id, block_id=block_id)

    def wages_on(self, on_date, job_type=None, block_id=None):
        """(daily wage, OT rate) on a date for this employee's (or the given) job type."""
        return (job_type or self.job_type).wages_on(on_date, self.work_context(block_id))

    def save(self, *args, **kwargs):
        if not self.employee_code:
            prefix = appsettings.current().employee_code_prefix or "EMP"
            numbers = [
                int(c[len(prefix) :])
                for c in Employee.objects.filter(employee_code__regex=rf"^{re.escape(prefix)}[0-9]+$").values_list(
                    "employee_code", flat=True
                )
            ]
            self.employee_code = f"{prefix}{(max(numbers) if numbers else 0) + 1:04d}"
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# Wage history
# ---------------------------------------------------------------------------
WAGE_WORKERS = [
    ("all", "Everybody (employees + labour count)"),
    ("employees", "Employees only"),
    ("count", "Daily Labour Count only"),
]


class WorkContext:
    """Who did the work, for choosing the wage rate: a registered employee (with their employee type)
    or the Daily Labour Count, on which plot."""

    __slots__ = ("employee_id", "employment_type_id", "block_id", "is_count")

    def __init__(self, employee_id=None, employment_type_id=None, block_id=None, is_count=False):
        self.employee_id, self.employment_type_id = employee_id, employment_type_id
        self.block_id, self.is_count = block_id, is_count


def rate_fits(r, ctx):
    """Does wage rate `r` apply to this work? Every field filled on the rate must match."""
    if r.employee_id and (ctx.is_count or r.employee_id != ctx.employee_id):
        return False
    if r.block_id and r.block_id != ctx.block_id:
        return False
    if r.employment_type_id and (ctx.is_count or r.employment_type_id != ctx.employment_type_id):
        return False
    if r.workers == "employees" and ctx.is_count:
        return False
    return not (r.workers == "count" and not ctx.is_count)


def rate_score(r):
    """More precise rates win: one employee > plot > employee type > employees-only / count-only > all."""
    return (
        (8 if r.employee_id else 0)
        + (4 if r.block_id else 0)
        + (2 if r.employment_type_id else 0)
        + (1 if r.workers != "all" else 0)
    )


def pick_rate(rates, on_date, ctx=None):
    """
    The rate for one piece of work from `rates` (one job type, newest first): valid on the date, every
    filled field matching (see rate_fits), the most precise one (see rate_score); for equally precise
    rates the newest. None: the job type's default wage.
    """
    ctx = ctx or WorkContext()
    best, best_score = None, -1
    for r in rates:
        if r.effective_from <= on_date and (r.effective_to is None or r.effective_to >= on_date) and rate_fits(r, ctx):
            score = rate_score(r)
            if score > best_score:  # rates come newest first: a tie keeps the newer one
                best, best_score = r, score
    return best


def _rates_between(d1, d2):
    return (
        WageRate.objects.filter(effective_from__lte=d2)
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=d1))
        .order_by("-effective_from", "-id")
    )


def wage_rate_on(job_type_id, on_date, ctx=None):
    """Wage rate of a job type for one piece of work on a date (see pick_rate)."""
    if not job_type_id or not on_date:
        return None
    return pick_rate(_rates_between(on_date, on_date).filter(job_type_id=job_type_id), on_date, ctx)


class WageBook:
    """All job types and their wage history for a period, loaded once (payroll of many employees)."""

    def __init__(self, d1, d2):
        self.jobs = {j.pk: j for j in JobType.objects.all()}
        self.rates = {}
        for r in _rates_between(d1, d2):
            self.rates.setdefault(r.job_type_id, []).append(r)

    def wages_on(self, job_type_id, on_date, ctx=None):
        job = self.jobs[job_type_id]
        return job.wages_from(pick_rate(self.rates.get(job_type_id, ()), on_date, ctx))


class WageRate(TimeStampedModel):
    """Salary details: the daily wage and OT rate of a job type from a date to a date - for everybody, or
    only for one plot, one employee, one employee type, or only registered employees / the Daily Labour
    Count. Work gets the most precise rate that fits it (see pick_rate); the rest keep the general one."""

    job_type = models.ForeignKey(JobType, verbose_name="Job type", on_delete=models.CASCADE, related_name="wage_rates")
    block = models.ForeignKey(
        Block,
        verbose_name="Plot",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="wage_rates",
        help_text="Only for work on this plot. Leave blank for all plots",
    )
    employee = models.ForeignKey(
        Employee,
        verbose_name="Employee",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="wage_rates",
        help_text="Only for this employee. Leave blank for everybody",
    )
    employment_type = models.ForeignKey(
        EmploymentType,
        verbose_name="Employee type",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        help_text="Leave blank for all employee types",
    )
    workers = models.CharField(
        "Applies to",
        max_length=10,
        choices=WAGE_WORKERS,
        default="all",
        help_text="Registered employees, the Daily Labour Count, or both",
    )
    rate = money_field("Daily wage")
    ot_rate_per_hour = money_field("OT rate / hour", help_text="0 = use the job type's default OT rate")
    effective_from = models.DateField("Date from", default=timezone.localdate)
    effective_to = models.DateField("Date to", null=True, blank=True, help_text="Leave blank if still in force")
    remarks = models.CharField("Remarks", max_length=200, blank=True, help_text="e.g. As per wage agreement 2026")

    SCOPE = ("job_type_id", "block_id", "employee_id", "employment_type_id", "workers")

    class Meta:
        ordering = ["-effective_from", "job_type__name"]
        indexes = [models.Index(fields=["job_type", "effective_from"])]
        verbose_name = "Wage rate"
        verbose_name_plural = "Wage rates"

    def __str__(self):
        to = f"{self.effective_to:%d/%m/%y}" if self.effective_to else "…"
        only = [str(x) for x in (self.employee, self.block, self.employment_type) if x is not None]
        if self.workers != "all":
            only.append(str(self.get_workers_display()))
        only = f" [{', '.join(only)}]" if only else ""
        return f"{self.job_type}{only}: {self.rate} ({self.effective_from:%d/%m/%y} - {to})"

    def scope_key(self):
        return tuple(getattr(self, name) for name in self.SCOPE)

    def _same_scope(self):
        return WageRate.objects.filter(**{name.removesuffix("_id"): getattr(self, name) for name in self.SCOPE})

    def clean(self):
        if self.effective_to and self.effective_from and self.effective_to < self.effective_from:
            raise ValidationError({"effective_to": "'Date to' must be after 'Date from'."})
        if self.employee_id:  # one employee: their type doesn't matter, and they are never in a count
            self.employment_type = None
            if self.workers == "count":
                raise ValidationError(
                    {"workers": "A rate for one employee can't be for the Daily Labour Count (no names there)."}
                )
        if self.employment_type_id and self.workers == "count":
            raise ValidationError(
                {"employment_type": "Employee types are for registered employees, not the Daily Labour Count."}
            )
        if self.job_type_id and self.effective_from and not getattr(self, "_skip_overlap_check", False):
            clash = self.overlapping().first()
            if clash:
                raise ValidationError(
                    f"This period overlaps the existing rate {clash}. Set a 'Date to' on the older rate first."
                )

    def overlapping(self):
        """Rates for exactly the same work (job, plot, employee, employee type, applies to) whose period
        overlaps this one. An older rate with no end date is not a clash: saving closes it automatically."""
        end = self.effective_to or dt.date.max
        qs = (
            self._same_scope()
            .filter(effective_from__lte=end)
            .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=self.effective_from))
            .exclude(effective_to__isnull=True, effective_from__lt=self.effective_from)
        )
        return qs.exclude(pk=self.pk) if self.pk else qs

    def _previous(self, start):
        """The rate for the same work that was auto-closed the day before `start`."""
        return self._same_scope().filter(effective_to=start - dt.timedelta(days=1)).exclude(pk=self.pk).first()

    def save(self, *args, **kwargs):
        if self.ot_rate_per_hour is None:
            self.ot_rate_per_hour = 0
        if self.employee_id:
            self.employment_type = None
        old = (
            WageRate.objects.filter(pk=self.pk).values("job_type_id", "effective_from", "effective_to").first()
            if self.pk
            else None
        )
        super().save(*args, **kwargs)
        if old and old["effective_from"] != self.effective_from:
            prev = self._previous(old["effective_from"])
            if prev and prev.effective_from < self.effective_from:
                # the start moved: the previous rate now runs until the new start
                WageRate.objects.filter(pk=prev.pk).update(effective_to=self.effective_from - dt.timedelta(days=1))
        if not getattr(self, "_close_later", False):  # set when the caller saves several rates and closes once
            close_open_rates(self.job_type_id)
        # work already entered in this period gets the new rate (unless its wage was typed by hand)
        starts = [self.effective_from] + ([old["effective_from"]] if old else [])
        ends = [self.effective_to] + ([old["effective_to"]] if old else [])
        reapply_rates(self.job_type_id, min(starts), None if None in ends else max(ends))
        if old and old["job_type_id"] != self.job_type_id:
            reapply_rates(old["job_type_id"], old["effective_from"], old["effective_to"])

    def delete(self, *args, **kwargs):
        prev = self._previous(self.effective_from)
        job_id, start, end = self.job_type_id, self.effective_from, self.effective_to
        result = super().delete(*args, **kwargs)
        if prev:
            # give the dates back to the rate before it, so they don't fall back to the default wage
            WageRate.objects.filter(pk=prev.pk).update(effective_to=self.effective_to)
            close_open_rates(job_id)
        reapply_rates(job_id, start, end)
        return result


def close_open_rates(job_type_id):
    """A rate with no 'Date to' runs until the day before the next rate for the same work starts."""
    groups = {}
    for r in WageRate.objects.filter(job_type_id=job_type_id).order_by("effective_from", "id"):
        groups.setdefault(r.scope_key(), []).append(r)
    for rates in groups.values():
        for cur, nxt in pairwise(rates):
            if cur.effective_to is None and nxt.effective_from > cur.effective_from:
                WageRate.objects.filter(pk=cur.pk).update(effective_to=nxt.effective_from - dt.timedelta(days=1))


def reapply_rates(job_type_id, d1, d2=None):
    """Individual work entries and Daily Labour Counts of a job type between two dates (d2 None = no end)
    take the wage and OT rate now in force for them. Wages typed by hand on an entry are kept."""
    from .. import productivity
    from . import workforce

    if not job_type_id or not d1:
        return
    with productivity.paused():
        for model in (workforce.WorkEntry, workforce.GroupWorkEntry):
            qs = model.objects.filter(job_type_id=job_type_id, date__gte=d1).filter(Q(wage_auto=True) | Q(ot_auto=True))
            if d2:
                qs = qs.filter(date__lte=d2)
            if model is workforce.WorkEntry:
                qs = qs.select_related("employee")
            for entry in qs.iterator(chunk_size=500):
                entry.save()
