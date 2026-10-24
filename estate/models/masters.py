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

    def rate_on(self, on_date, employment_type_id=None):
        return wage_rate_on(self.pk, on_date, employment_type_id)

    def wages_on(self, on_date, employment_type_id=None):
        """(daily wage, OT rate / hour) on a date: wage history first, else the defaults."""
        return self.wages_from(self.rate_on(on_date, employment_type_id))

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

    def wages_on(self, on_date, job_type=None):
        """(daily wage, OT rate) on a date for this employee's (or the given) job type."""
        return (job_type or self.job_type).wages_on(on_date, self.employment_type_id)

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
def pick_rate(rates, on_date, employment_type_id=None):
    """
    The rate valid on a date from `rates` (one job type, newest first). A rate for the employee's
    own type wins over an "all employee types" rate.
    """
    valid = [r for r in rates if r.effective_from <= on_date and (r.effective_to is None or r.effective_to >= on_date)]
    if employment_type_id:
        for r in valid:
            if r.employment_type_id == employment_type_id:
                return r
    return next((r for r in valid if r.employment_type_id is None), None)


def _rates_between(d1, d2):
    return (
        WageRate.objects.filter(effective_from__lte=d2)
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=d1))
        .order_by("-effective_from", "-id")
    )


def wage_rate_on(job_type_id, on_date, employment_type_id=None):
    """Wage rate of a job type valid on a date (see pick_rate)."""
    if not job_type_id or not on_date:
        return None
    return pick_rate(_rates_between(on_date, on_date).filter(job_type_id=job_type_id), on_date, employment_type_id)


class WageBook:
    """All job types and their wage history for a period, loaded once (payroll of many employees)."""

    def __init__(self, d1, d2):
        self.jobs = {j.pk: j for j in JobType.objects.all()}
        self.rates = {}
        for r in _rates_between(d1, d2):
            self.rates.setdefault(r.job_type_id, []).append(r)

    def wages_on(self, job_type_id, on_date, employment_type_id=None):
        job = self.jobs[job_type_id]
        return job.wages_from(pick_rate(self.rates.get(job_type_id, ()), on_date, employment_type_id))


class WageRate(TimeStampedModel):
    """Module 4 - Salary details: wage of a job type (optionally for one employee type) from a date to a date."""

    job_type = models.ForeignKey(JobType, verbose_name="Job type", on_delete=models.CASCADE, related_name="wage_rates")
    employment_type = models.ForeignKey(
        EmploymentType,
        verbose_name="Applies to",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        help_text="Leave blank for all employee types",
    )
    rate = money_field("Daily wage")
    ot_rate_per_hour = money_field("OT rate / hour", help_text="0 = use the job type's default OT rate")
    effective_from = models.DateField("Date from", default=timezone.localdate)
    effective_to = models.DateField("Date to", null=True, blank=True, help_text="Leave blank if still in force")
    remarks = models.CharField("Remarks", max_length=200, blank=True, help_text="e.g. As per wage agreement 2026")

    class Meta:
        ordering = ["-effective_from", "job_type__name"]
        indexes = [models.Index(fields=["job_type", "effective_from"])]
        verbose_name = "Wage rate"
        verbose_name_plural = "Wage rates"

    def __str__(self):
        to = f"{self.effective_to:%d/%m/%y}" if self.effective_to else "…"
        return f"{self.job_type}: {self.rate} ({self.effective_from:%d/%m/%y} - {to})"

    def clean(self):
        if self.effective_to and self.effective_from and self.effective_to < self.effective_from:
            raise ValidationError({"effective_to": "'Date to' must be after 'Date from'."})
        if self.job_type_id and self.effective_from and not getattr(self, "_skip_overlap_check", False):
            clash = self.overlapping().first()
            if clash:
                raise ValidationError(
                    f"This period overlaps the existing rate {clash}. Set a 'Date to' on the older rate first."
                )

    def overlapping(self):
        """Rates of the same job + employee type whose period overlaps this one.
        An older rate with no end date is not a clash: saving closes it automatically."""
        end = self.effective_to or dt.date.max
        qs = (
            WageRate.objects.filter(
                job_type_id=self.job_type_id, employment_type_id=self.employment_type_id, effective_from__lte=end
            )
            .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=self.effective_from))
            .exclude(effective_to__isnull=True, effective_from__lt=self.effective_from)
        )
        return qs.exclude(pk=self.pk) if self.pk else qs

    def _previous(self, start):
        """The rate that was auto-closed the day before `start` (same job + employee type)."""
        return (
            WageRate.objects.filter(
                job_type_id=self.job_type_id,
                employment_type_id=self.employment_type_id,
                effective_to=start - dt.timedelta(days=1),
            )
            .exclude(pk=self.pk)
            .first()
        )

    def save(self, *args, **kwargs):
        if self.ot_rate_per_hour is None:
            self.ot_rate_per_hour = 0
        old = WageRate.objects.filter(pk=self.pk).values("effective_from").first() if self.pk else None
        super().save(*args, **kwargs)
        if old and old["effective_from"] != self.effective_from:
            prev = self._previous(old["effective_from"])
            if prev and prev.effective_from < self.effective_from:
                # the start moved: the previous rate now runs until the new start
                WageRate.objects.filter(pk=prev.pk).update(effective_to=self.effective_from - dt.timedelta(days=1))
        if not getattr(self, "_close_later", False):  # set when the caller saves several rates and closes once
            close_open_rates(self.job_type_id)

    def delete(self, *args, **kwargs):
        prev = self._previous(self.effective_from)
        result = super().delete(*args, **kwargs)
        if prev:
            # give the dates back to the rate before it, so they don't fall back to the default wage
            WageRate.objects.filter(pk=prev.pk).update(effective_to=self.effective_to)
            close_open_rates(self.job_type_id)
        return result


def close_open_rates(job_type_id):
    """A rate with no 'Date to' runs until the day before the next rate (same job + employee type) starts."""
    groups = {}
    for r in WageRate.objects.filter(job_type_id=job_type_id).order_by("effective_from", "id"):
        groups.setdefault(r.employment_type_id, []).append(r)
    for rates in groups.values():
        for cur, nxt in pairwise(rates):
            if cur.effective_to is None and nxt.effective_from > cur.effective_from:
                WageRate.objects.filter(pk=cur.pk).update(effective_to=nxt.effective_from - dt.timedelta(days=1))
