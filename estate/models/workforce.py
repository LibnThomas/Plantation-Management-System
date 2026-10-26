from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.i18n import ui

from ..productivity import RefreshHarvests
from .base import POSITIVE, TimeStampedModel, money_field, q2, qty_field
from .lookups import AttendanceStatus, PaymentMode, PaymentStatus
from .masters import Block, Employee, GangLeader, JobType, WorkContext


class AutoRates:
    """Work entries and labour counts: the wage and OT rate come from the wage list (Salary Details) for the
    entry's date, job, plot and worker, and follow it when it changes later - unless one was typed by hand
    (different from the list), which is then kept. `wage_auto` / `ot_auto` remember which ones follow."""

    WAGE_FIELD, OT_FIELD = "wage", "ot_rate"

    def work_context(self):
        raise NotImplementedError

    def apply_rates(self):
        if not self.job_type_id:
            return
        listed = self.job_type.wages_on(self.date, self.work_context())
        prev = None
        if self.pk:
            prev = (
                type(self)
                .objects.filter(pk=self.pk)
                .values(self.WAGE_FIELD, self.OT_FIELD, "wage_auto", "ot_auto")
                .first()
            )
        for name, flag, value in ((self.WAGE_FIELD, "wage_auto", listed[0]), (self.OT_FIELD, "ot_auto", listed[1])):
            current = getattr(self, name)
            follows = (
                not current
                or current == value
                # unchanged since it was filled from the list: keeps following the list
                or bool(prev and prev[flag] and current == prev[name])
            )
            setattr(self, flag, follows)
            if follows:
                setattr(self, name, value)


class Attendance(RefreshHarvests, TimeStampedModel):
    """Module 2 - Attendance."""

    employee = models.ForeignKey(Employee, verbose_name="Employee", on_delete=models.CASCADE, related_name="attendance")
    date = models.DateField("Date", default=timezone.localdate)
    status = models.ForeignKey(AttendanceStatus, verbose_name="Attendance", on_delete=models.PROTECT)
    job_type = models.ForeignKey(
        JobType,
        verbose_name="Work done",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text="Leave blank to use the employee's usual job",
    )
    block = models.ForeignKey(Block, verbose_name="Block / Plot", null=True, blank=True, on_delete=models.SET_NULL)
    ot_hours = models.DecimalField("Overtime hours", max_digits=5, decimal_places=2, default=0, validators=POSITIVE)
    remarks = models.CharField("Remarks", max_length=200, blank=True)

    refresh_whole_day = True  # present / absent changes the people counted for harvests of this date

    class Meta:
        ordering = ["-date", "employee__full_name"]
        indexes = [models.Index(fields=["date"])]
        constraints = [models.UniqueConstraint(fields=["employee", "date"], name="one_attendance_per_day")]
        verbose_name = "Attendance"
        verbose_name_plural = "Attendance"

    def __str__(self):
        return f"{self.employee.full_name} - {self.date} - {self.status}"


class WorkEntry(AutoRates, RefreshHarvests, TimeStampedModel):
    """Module 3a - Work details of one worker for one day."""

    date = models.DateField("Date", default=timezone.localdate)
    employee = models.ForeignKey(
        Employee, verbose_name="Employee", on_delete=models.PROTECT, related_name="work_entries"
    )
    job_type = models.ForeignKey(JobType, verbose_name="Work type", on_delete=models.PROTECT)
    block = models.ForeignKey(Block, verbose_name="Block / Plot", null=True, blank=True, on_delete=models.SET_NULL)
    wage = money_field("Wage", help_text="Filled from the work type. You can change it.")
    ot_hours = models.DecimalField("Overtime hours", max_digits=5, decimal_places=2, default=0, validators=POSITIVE)
    ot_rate = money_field("OT rate / hour", help_text="Filled from the work type. You can change it.")
    ot_amount = money_field("Overtime amount", editable=False)
    output_quantity = qty_field("Output quantity", help_text="Optional, e.g. kg picked by this worker")
    total_amount = money_field("Total amount", editable=False)
    wage_auto = models.BooleanField("Wage from the wage list", default=True, editable=False)
    ot_auto = models.BooleanField("OT rate from the wage list", default=True, editable=False)
    remarks = models.TextField("Comments", blank=True)

    class Meta:
        ordering = ["-date", "employee__full_name"]
        indexes = [models.Index(fields=["date"]), models.Index(fields=["employee", "date"])]
        verbose_name = "Work entry (individual)"
        verbose_name_plural = "Work entries (individual)"

    def __str__(self):
        return f"{self.date:%d/%m/%y} - {self.employee.full_name} - {self.job_type}"

    def work_context(self):
        if self.employee_id:
            return self.employee.work_context(self.block_id)
        return WorkContext(block_id=self.block_id)

    def save(self, *args, **kwargs):
        # a wage / OT rate typed here wins; otherwise the rate in force for this date, job, plot and employee
        self.apply_rates()
        self.ot_amount = q2((self.ot_hours or 0) * (self.ot_rate or 0))
        self.total_amount = q2((self.wage or 0) + self.ot_amount)
        super().save(*args, **kwargs)


def count_field(verbose_name, **kw):
    """Number of workers; halves allowed (13.5 = one of 14 went home at noon)."""
    return models.DecimalField(verbose_name, max_digits=6, decimal_places=1, default=0, validators=POSITIVE, **kw)


class GroupWorkEntry(AutoRates, RefreshHarvests, TimeStampedModel):
    """Module 3b - Daily Labour Count: day labourers who are not registered, entered only as a number
    (e.g. 10 cardamom pickers today). Payroll pays them per gang leader; registered employees are
    never part of a count (they are paid from attendance / individual work)."""

    date = models.DateField("Date", default=timezone.localdate)
    gang_leader = models.ForeignKey(
        GangLeader,
        verbose_name="Gang leader / Paid to",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="labour_counts",
        help_text="Who brings these workers and receives their pay",
    )
    job_type = models.ForeignKey(JobType, verbose_name="Work type", on_delete=models.PROTECT)
    block = models.ForeignKey(Block, verbose_name="Block / Plot", null=True, blank=True, on_delete=models.SET_NULL)
    workers_count = count_field(
        "Total no. of workers",
        help_text="Filled automatically when male / female workers are entered. Half days allowed, e.g. 13.5",
    )
    male_workers = count_field("Male workers")
    female_workers = count_field("Female workers")
    wage_per_worker = money_field("Wage per worker", help_text="Filled from the work type. You can change it.")
    total_wages = money_field("Total wages", editable=False)
    ot_rate_per_hour = money_field("OT rate / hour", help_text="Filled from the work type. You can change it.")
    wage_auto = models.BooleanField("Wage from the wage list", default=True, editable=False)
    ot_auto = models.BooleanField("OT rate from the wage list", default=True, editable=False)
    WAGE_FIELD, OT_FIELD = "wage_per_worker", "ot_rate_per_hour"
    ot_morning_workers = count_field("OT workers (morning)")
    ot_morning_hours = models.DecimalField(
        "OT work hours (morning)",
        max_digits=5,
        decimal_places=2,
        default=0,
        validators=POSITIVE,
        help_text="Hours each morning OT worker worked",
    )
    ot_evening_workers = count_field("OT workers (evening)")
    ot_evening_hours = models.DecimalField(
        "OT work hours (evening)",
        max_digits=5,
        decimal_places=2,
        default=0,
        validators=POSITIVE,
        help_text="Hours each evening OT worker worked",
    )
    ot_total_hours = models.DecimalField(
        "Total OT hours (all workers)", max_digits=8, decimal_places=2, default=0, editable=False
    )
    ot_amount = money_field("Overtime amount", editable=False)
    total_cost = money_field("Total cost", editable=False)
    area_covered_acres = models.DecimalField(
        "Area covered (acres)", max_digits=8, decimal_places=2, default=0, validators=POSITIVE
    )
    remarks = models.TextField("Comments", blank=True)

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [models.Index(fields=["date"])]
        verbose_name = "Daily labour count"
        verbose_name_plural = "Daily labour counts"

    def work_context(self):
        return WorkContext(block_id=self.block_id, is_count=True)

    def __str__(self):
        leader = f" ({self.gang_leader})" if self.gang_leader_id else ""
        return f"{self.date:%d/%m/%y} - {self.job_type} - {self.workers_count}{leader}"

    def save(self, *args, **kwargs):
        if self.male_workers or self.female_workers:
            self.workers_count = (self.male_workers or 0) + (self.female_workers or 0)
        self.apply_rates()
        self.total_wages = q2(self.workers_count * (self.wage_per_worker or 0))
        self.ot_total_hours = q2(
            (self.ot_morning_workers or 0) * (self.ot_morning_hours or 0)
            + (self.ot_evening_workers or 0) * (self.ot_evening_hours or 0)
        )
        self.ot_amount = q2(self.ot_total_hours * (self.ot_rate_per_hour or 0))
        self.total_cost = q2(self.total_wages + self.ot_amount)
        super().save(*args, **kwargs)


PAY_STATES = [
    ("unpaid", "Not paid"),
    ("part", "Part paid"),
    ("paid", "Paid"),
    ("carried", "Balance moved to a later payroll"),
]
# one payment entry (SalaryPaid): only 'paid' entries count; 'pending' is recorded but not paid yet
ENTRY_STATES = [("paid", "Paid"), ("pending", "Pending (not paid)")]


class SalaryPayment(TimeStampedModel):
    """Payroll: what was paid for a period - to a registered employee, or for the Daily Labour Count
    workers of a gang leader (employee empty; gang leader set, or both empty for counts without one)."""

    employee = models.ForeignKey(
        Employee,
        verbose_name="Employee",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="salary_payments",
    )
    gang_leader = models.ForeignKey(
        GangLeader,
        verbose_name="Gang leader / Paid to",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="salary_payments",
        help_text="Only for Daily Labour Count payments",
    )
    period_from = models.DateField("Period from")
    period_to = models.DateField("Period to")
    full_days = models.DecimalField("Full days", max_digits=5, decimal_places=1, default=0, validators=POSITIVE)
    half_days = models.DecimalField("Half days", max_digits=5, decimal_places=1, default=0, validators=POSITIVE)
    leave_days = models.DecimalField("Leave days", max_digits=5, decimal_places=1, default=0, validators=POSITIVE)
    days_worked = models.DecimalField(
        "Days worked (payable)", max_digits=6, decimal_places=2, default=0, editable=False
    )
    daily_rate = money_field("Daily rate", help_text="Leave 0 to use the job type's wage for the period")
    basic_amount = money_field("Basic amount", editable=False)
    ot_hours = models.DecimalField("OT hours", max_digits=6, decimal_places=2, default=0, validators=POSITIVE)
    ot_rate = money_field("OT rate / hour", help_text="Leave 0 to use the job type's OT rate for the period")
    ot_amount = money_field("OT amount", editable=False)
    bonus = money_field("Bonus / Incentive")
    advance_deduction = money_field("Advance deduction")
    other_deduction = money_field("Other deductions (PF/ESI etc.)")
    gross_amount = money_field("Gross amount", editable=False)
    net_amount = money_field("Net payable", editable=False)
    payment_date = models.DateField("Payment date", null=True, blank=True)
    payment_mode = models.ForeignKey(
        PaymentMode, verbose_name="Payment mode", null=True, blank=True, on_delete=models.SET_NULL
    )
    status = models.ForeignKey(PaymentStatus, verbose_name="Status", on_delete=models.PROTECT)
    remarks = models.CharField("Remarks", max_length=200, blank=True)
    # --- paying it: one SalaryPaid row per payment made; an unpaid balance can move to a later payroll ---
    previous_pending = money_field(
        "Previous pending (added)", editable=False, help_text="Unpaid balances of earlier payrolls added to this one"
    )
    amount_paid = money_field("Amount paid", editable=False)
    carried_amount = money_field("Moved to a later payroll", editable=False)
    carried_to = models.ForeignKey(
        "self",
        verbose_name="Balance moved to",
        null=True,
        blank=True,
        editable=False,
        on_delete=models.SET_NULL,
        related_name="carried_from",
    )
    balance = money_field("Balance (pending)", editable=False)
    pay_state = models.CharField(
        "Payment", max_length=10, choices=PAY_STATES, default="unpaid", editable=False, db_index=True
    )
    blocks = models.ManyToManyField(
        Block,
        verbose_name="Plots",
        blank=True,
        related_name="salary_payments",
        help_text="The plots whose work this salary pays for. None: all plots",
    )
    # Filled by Generate Payroll: one line per (source, wage, OT rate) actually used
    breakdown = models.JSONField("Calculation breakdown", default=list, blank=True, editable=False)

    CALCULATED = ("full_days", "half_days", "leave_days", "daily_rate", "ot_hours", "ot_rate")
    STR_RELATED = ("employee", "gang_leader")  # used by __str__: lists load them in the same query

    class Meta:
        ordering = ["-period_to", "employee__full_name"]
        indexes = [
            models.Index(fields=["period_to"]),
            models.Index(fields=["employee", "period_to"]),
            models.Index(fields=["gang_leader", "period_to"]),
        ]
        verbose_name = "Salary payment"
        verbose_name_plural = "Salary payments"

    def __str__(self):
        # the plots only when already loaded (prefetch_related("blocks")): no extra query per row in lists
        cached = getattr(self, "_prefetched_objects_cache", {}).get("blocks")
        plot = f" ({', '.join(b.name for b in cached)})" if cached else ""
        return f"{self.payee_name}{plot}: {self.period_from:%d/%m/%y} - {self.period_to:%d/%m/%y}"

    @property
    def plot_ids(self):
        """The plots this payroll is for; empty = all plots."""
        return {b.pk for b in self.blocks.all()} if self.pk else set()

    @property
    def plots_text(self):
        return ", ".join(b.name for b in self.blocks.all()) if self.pk else ""

    @property
    def is_labour_count(self):
        """Payment for unregistered day labourers (Daily Labour Count), not for an employee."""
        return not self.employee_id

    @property
    def payee_name(self):
        if self.employee_id:
            return self.employee.full_name
        return str(self.gang_leader) if self.gang_leader_id else ui("Daily labour count (no gang leader)")

    def clean(self):
        if self.period_to and self.period_from and self.period_to < self.period_from:
            raise ValidationError({"period_to": "'Period to' must be after 'Period from'."})
        if self.employee_id and self.gang_leader_id:
            raise ValidationError(ui("Choose an employee or a gang leader, not both."))
        if not self.employee_id and not self.gang_leader_id and not self.breakdown:
            raise ValidationError({"employee": ui("Choose the employee (or the gang leader for day labourers).")})

    def locked_fields(self):
        """Fields that come from the payroll calculation and can't be edited by hand."""
        return (*self.CALCULATED, "blocks") if self.breakdown else ()

    def save(self, *args, **kwargs):
        if self.breakdown:
            # the exact part of each day that was paid (statuses may pay 0.5, 0.75, 1.5 ...)
            self.days_worked = sum((Decimal(line["days"]) for line in self.breakdown), Decimal(0))
            self.basic_amount = q2(sum(Decimal(line["basic"]) for line in self.breakdown))
            self.ot_amount = q2(sum(Decimal(line["ot_amount"]) for line in self.breakdown))
            self.daily_rate = q2(self.basic_amount / self.days_worked) if self.days_worked else 0  # average
            self.ot_rate = q2(self.ot_amount / self.ot_hours) if self.ot_hours else 0
        else:
            self.days_worked = (self.full_days or 0) + (self.half_days or 0) * Decimal("0.5")
            if self.employee_id and (not self.daily_rate or not self.ot_rate):
                wage, ot = self.employee.wages_on(self.period_to or timezone.localdate())
                self.daily_rate = self.daily_rate or wage
                self.ot_rate = self.ot_rate or ot
            self.basic_amount = q2(self.days_worked * (self.daily_rate or 0))
            self.ot_amount = q2((self.ot_hours or 0) * (self.ot_rate or 0))
        self.gross_amount = q2(self.basic_amount + self.ot_amount + (self.bonus or 0))
        self.net_amount = q2(
            self.gross_amount
            - (self.advance_deduction or 0)
            - (self.other_deduction or 0)
            + (self.previous_pending or 0)
        )
        if self.pk:
            self.amount_paid = self.payments.filter(status="paid").aggregate(s=models.Sum("amount"))["s"] or 0
        self._set_balance()
        super().save(*args, **kwargs)

    def _set_balance(self):
        self.balance = q2(self.net_amount - (self.amount_paid or 0) - (self.carried_amount or 0))
        if self.carried_to_id and self.carried_amount:
            self.pay_state = "carried"
        elif self.balance <= 0 and (self.amount_paid or self.net_amount <= 0):
            self.pay_state = "paid"
        elif self.amount_paid:
            self.pay_state = "part"
        else:
            self.pay_state = "unpaid"

    def refresh_paid(self):
        """After a payment was added, changed or deleted: totals, balance after each payment, state, status.
        Only entries with the status 'Paid' count as paid; 'Pending' entries are recorded but not paid."""
        running = Decimal(0)
        payments = list(self.payments.order_by("date", "id"))
        for pay in payments:
            if pay.is_paid:
                running += pay.amount
            after = q2(self.net_amount - running)
            if pay.balance_after != after:
                SalaryPaid.objects.filter(pk=pay.pk).update(balance_after=after)
        self.amount_paid = running
        self._set_balance()
        changes = {"amount_paid": self.amount_paid, "balance": self.balance, "pay_state": self.pay_state}
        paid = [x for x in payments if x.is_paid]
        if paid:
            last = paid[-1]
            changes.update(payment_date=last.date, payment_mode_id=last.payment_mode_id or self.payment_mode_id)
        if self.pay_state in ("paid", "carried") and not self.status.is_final:
            final = PaymentStatus.objects.filter(is_active=True, is_final=True).order_by("sort_order", "id").first()
            if final:
                changes["status"] = final
        elif self.pay_state == "unpaid" and self.status.is_final:
            # nothing paid (only 'Pending' entries, or the paid ones deleted): the payroll is pending too
            pending = PaymentStatus.objects.filter(is_active=True, is_final=False).order_by("sort_order", "id").first()
            if pending:
                changes["status"] = pending
        for key, value in changes.items():
            setattr(self, key, value)
        type(self).objects.filter(pk=self.pk).update(**changes)

    def carry_into(self, later):
        """Move this payroll's unpaid balance into a later payroll (it is paid there)."""
        self.carried_amount = (self.carried_amount or 0) + self.balance
        self.carried_to = later
        self._set_balance()
        type(self).objects.filter(pk=self.pk).update(
            carried_amount=self.carried_amount, carried_to=later, balance=self.balance, pay_state=self.pay_state
        )

    def delete(self, *args, **kwargs):
        # balances that were moved into this payroll become pending again where they came from
        for old in list(self.carried_from.all()):
            old.carried_amount, old.carried_to = 0, None
            old._set_balance()
            type(old).objects.filter(pk=old.pk).update(
                carried_amount=0, carried_to=None, balance=old.balance, pay_state=old.pay_state
            )
        return super().delete(*args, **kwargs)

    @property
    def is_locked(self):
        """Paid (in part or full) or moved on: Generate Payroll must not re-calculate it."""
        return bool(self.amount_paid) or bool(self.carried_to_id) or self.status.is_final


class SalaryPaid(TimeStampedModel):
    """One payment made against a payroll (salary payment): 50,000 on 23/10, 10,000 on 24/10 ..."""

    salary = models.ForeignKey(
        SalaryPayment, verbose_name="Payroll (salary payment)", on_delete=models.PROTECT, related_name="payments"
    )
    date = models.DateField("Paid on", default=timezone.localdate)
    amount = money_field("Amount")
    payment_mode = models.ForeignKey(
        PaymentMode, verbose_name="Payment mode", null=True, blank=True, on_delete=models.SET_NULL
    )
    status = models.CharField(
        "Payment status",
        max_length=10,
        choices=ENTRY_STATES,
        default="paid",
        db_index=True,
        help_text="Only 'Paid' entries are counted as paid. 'Pending' = recorded, not paid yet.",
    )
    balance_after = money_field("Balance after this payment", editable=False)
    remarks = models.CharField("Remarks", max_length=200, blank=True, help_text="e.g. cheque no., paid by")

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [models.Index(fields=["date"])]
        verbose_name = "Salary paid (payment made)"
        verbose_name_plural = "Salary paid (payments made)"

    def __str__(self):
        return f"{self.date:%d/%m/%y} - {self.salary.payee_name} - {self.amount}"

    @property
    def is_paid(self):
        return self.status == "paid"

    def clean(self):
        if self.amount is not None and self.amount <= 0:
            raise ValidationError({"amount": ui("Enter the amount paid.")})
        if self.salary_id and self.amount:
            other = (
                self.salary.payments.exclude(pk=self.pk).filter(status="paid").aggregate(s=models.Sum("amount"))["s"]
                or 0
            )
            due = self.salary.net_amount - (self.salary.carried_amount or 0) - other
            if self.amount > due:
                raise ValidationError(
                    {"amount": ui("Only {due} is pending on this payroll.", due=f"{max(due, 0):,.2f}")}
                )

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.salary.refresh_paid()

    def delete(self, *args, **kwargs):
        salary = self.salary
        result = super().delete(*args, **kwargs)
        salary.refresh_paid()
        return result
