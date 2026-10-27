from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils import timezone

from core import appsettings
from core.i18n import ui

from ..productivity import RefreshHarvests
from .base import POSITIVE, ZERO, TimeStampedModel, money_field, q2, qty_field
from .lookups import (
    RECEIPT_STATES,
    ApplicationMethod,
    ExpenseCategory,
    OperationType,
    PaymentMode,
    ProcessType,
    ReceiptStatus,
)
from .masters import LINE_BASES, AgriInput, Block, JobType, Product, dosage_text

MIX_TYPES = [("water", "Mixed in water"), ("dry", "Dry mix (no water)")]


class FieldOperation(TimeStampedModel):
    """Module 6 - Fertilizer, spraying and other field work. The chemicals used are its lines
    (FieldOperationInput): each with its dosage, quantity and the purchase (price) it came from."""

    date = models.DateField("Date", default=timezone.localdate)
    operation_type = models.ForeignKey(OperationType, verbose_name="Operation", on_delete=models.PROTECT)
    block = models.ForeignKey(Block, verbose_name="Block / Plot", null=True, blank=True, on_delete=models.SET_NULL)
    method = models.ForeignKey(
        ApplicationMethod, verbose_name="Application method", null=True, blank=True, on_delete=models.SET_NULL
    )
    # --- the mix: chemicals in a jar of water, or a dry mix (no water); see the lines ---
    mix_type = models.CharField(
        "Mix",
        max_length=10,
        choices=MIX_TYPES,
        default="water",
        help_text="In water (spray / drench) or dry (no water)",
    )
    tank_litres = models.DecimalField(
        "Jar capacity (litres)",
        max_digits=10,
        decimal_places=2,
        default=0,
        blank=True,
        validators=POSITIVE,
        help_text="Water in one jar, e.g. 500 or 1000. The amounts follow it.",
    )
    mix_dose = models.DecimalField(
        "Mix given per plant (kg)",
        max_digits=10,
        decimal_places=3,
        default=0,
        blank=True,
        validators=POSITIVE,
        help_text="Dry mix: how much of the finished mix each plant gets, e.g. 2",
    )
    packet_size = models.DecimalField(
        "Packet size (kg)",
        max_digits=10,
        decimal_places=3,
        default=0,
        blank=True,
        validators=POSITIVE,
        help_text="Dry mix packed for later use, e.g. 20 kg packets",
    )
    rounds = models.DecimalField(
        "Rounds mixed",
        max_digits=8,
        decimal_places=2,
        default=0,
        blank=True,
        validators=POSITIVE,
        help_text="How many times this same mix was made (that day)",
    )
    mix_name = models.CharField(
        "Mix name", max_length=80, blank=True, help_text="Optional: name it to pick this mix again later"
    )
    # older entries: chemicals given per plant were multiplied by this
    plants_total = models.PositiveIntegerField("No. of plants", default=0, editable=False)
    purpose = models.CharField(
        "Purpose / Target pest or disease",
        max_length=120,
        blank=True,
        help_text="e.g. Thrips, Capsule rot, Nutrient round 2",
    )
    # no longer entered (the workers are in Work Details / Daily Labour Count); kept for older entries
    workers_count = models.PositiveIntegerField("No. of employees", default=0, editable=False)
    wage_per_worker = money_field("Wage per worker", editable=False)
    labour_cost = money_field("Labour cost", editable=False)
    area_covered_acres = models.DecimalField(
        "Area covered (acres)", max_digits=8, decimal_places=2, default=0, validators=POSITIVE
    )
    weather = models.CharField("Weather", max_length=60, blank=True)
    next_due_date = models.DateField("Next due date", null=True, blank=True, help_text="Reminder for the next round")
    chemicals_text = models.CharField("Chemicals used", max_length=400, blank=True, editable=False)
    input_cost = money_field("Fertilizer / chemical cost", editable=False)
    total_cost = money_field("Total cost", editable=False)
    remarks = models.TextField("Comments", blank=True)

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [models.Index(fields=["date"]), models.Index(fields=["next_due_date"])]
        verbose_name = "Fertilizer & field work"
        verbose_name_plural = "Fertilizer & field work"

    def __str__(self):
        return f"{self.date:%d/%m/%y} - {self.operation_type}"

    def chemical_qty(self, dose_qty, basis, dose_water):
        """Quantity of one chemical for the whole job: amount per mix x mix count (amounts follow the jar
        size), or amount per plant x plants, or per acre x acres."""
        dose = Decimal(dose_qty or 0)
        if not dose:
            return ZERO
        if basis == "plant":
            return dose * (self.plants_total or 0)
        if basis == "acre":
            return dose * (self.area_covered_acres or 0)
        if basis == "batch":  # dry mix: amount in one batch x batches made
            return (dose * (self.rounds or Decimal(1))).quantize(Decimal("0.001"))
        per_mix = dose * Decimal(self.tank_litres) / Decimal(dose_water) if self.tank_litres and dose_water else dose
        return (per_mix * (self.rounds or Decimal(1))).quantize(Decimal("0.001"))

    @property
    def water_total(self):
        return (self.tank_litres or 0) * (self.rounds or 0)

    def save(self, *args, **kwargs):
        for name in ("tank_litres", "mix_dose", "packet_size", "rounds"):
            if getattr(self, name) is None:  # left empty on the form
                setattr(self, name, 0)
        self.labour_cost = q2((self.workers_count or 0) * (self.wage_per_worker or 0))
        self.total_cost = q2((self.input_cost or 0) + self.labour_cost)
        super().save(*args, **kwargs)

    def update_costs(self, using=None):
        """Chemical cost and the 'chemicals used' text from the lines (after lines are added / changed)."""
        using = using or self._state.db or "default"
        lines = list(self.chemicals.using(using).select_related("agri_input__unit").order_by("id"))
        self.input_cost = q2(sum((x.cost for x in lines), ZERO))
        names = [f"{x.agri_input.name} {_qty(x.quantity)} {x.agri_input.unit_name}".strip() for x in lines]
        self.chemicals_text = ", ".join(names)[:400]
        self.total_cost = q2(self.input_cost + (self.labour_cost or 0))
        type(self).objects.using(using).filter(pk=self.pk).update(
            input_cost=self.input_cost, chemicals_text=self.chemicals_text, total_cost=self.total_cost
        )


def _qty(value):
    text = f"{value or 0:f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


class AgriInputPurchase(TimeStampedModel):
    """A purchase of a fertilizer / chemical: how many bags / bottles, at what price, from whom.
    Each purchase keeps its own price: field work that used it is costed at that price, so later price
    changes never alter old entries."""

    agri_input = models.ForeignKey(
        AgriInput, verbose_name="Fertilizer / Chemical", on_delete=models.PROTECT, related_name="purchases"
    )
    date = models.DateField("Purchase date", default=timezone.localdate)
    bags = models.DecimalField("Bags / bottles bought", max_digits=10, decimal_places=2, default=0, validators=POSITIVE)
    pack_size = models.DecimalField(
        "Quantity per bag / bottle",
        max_digits=12,
        decimal_places=3,
        default=0,
        validators=POSITIVE,
        help_text="In the chemical's unit. Filled from the chemical; you can change it.",
    )
    price_per_bag = money_field("Amount per bag / bottle")
    supplier = models.CharField("Purchased from", max_length=120, blank=True)
    invoice_no = models.CharField("Bill / invoice no.", max_length=40, blank=True)
    remarks = models.TextField("Comments", blank=True)
    quantity = models.DecimalField("Total quantity", max_digits=14, decimal_places=3, default=0, editable=False)
    total_amount = money_field("Total amount", editable=False)
    unit_cost = models.DecimalField("Cost per unit", max_digits=14, decimal_places=4, default=0, editable=False)
    used_qty = models.DecimalField("Used", max_digits=14, decimal_places=3, default=0, editable=False)
    left_qty = models.DecimalField("Left in stock", max_digits=14, decimal_places=3, default=0, editable=False)

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "Fertilizer / chemical purchase"
        verbose_name_plural = "Fertilizer / chemical purchases"

    def __str__(self):
        bits = [f"{self.date:%d/%m/%y}", self.agri_input.name if self.agri_input_id else ""]
        if self.supplier:
            bits.append(self.supplier)
        return " - ".join(b for b in bits if b)

    def save(self, *args, **kwargs):
        if not self.pack_size and self.agri_input_id:
            self.pack_size = self.agri_input.pack_size or Decimal(1)
        pack = Decimal(self.pack_size or 0)
        self.quantity = (Decimal(self.bags or 0) * pack).quantize(Decimal("0.001"))
        self.total_amount = q2(Decimal(self.bags or 0) * Decimal(self.price_per_bag or 0))
        self.unit_cost = (
            Decimal(self.price_per_bag or 0) / pack if pack else Decimal(self.price_per_bag or 0)
        ).quantize(Decimal("0.0001"))
        self.left_qty = self.quantity - (self.used_qty or 0)
        old_input = None
        if self.pk:
            old_input = (
                type(self)
                .objects.using(self._state.db or "default")
                .filter(pk=self.pk)
                .values_list("agri_input", flat=True)
                .first()
            )
        super().save(*args, **kwargs)
        self.refresh_used()
        self.agri_input.refresh_stock()
        if old_input and old_input != self.agri_input_id:
            AgriInput.objects.using(self._state.db).get(pk=old_input).refresh_stock()

    def refresh_used(self, using=None):
        from django.db.models import Sum

        using = using or self._state.db or "default"
        used = self.uses.using(using).aggregate(t=Sum("quantity"))["t"] or ZERO
        self.used_qty, self.left_qty = used, self.quantity - used
        type(self).objects.using(using).filter(pk=self.pk).update(used_qty=used, left_qty=self.left_qty)

    def option_label(self):
        """For the purchase drop-down: '12/01/26 · Agro Traders · ₹250 / bag (₹10 / kg) · 20 kg left'"""
        unit = self.agri_input.unit_name
        cur = appsettings.current().currency_symbol
        parts = [f"{self.date:%d/%m/%y}"]
        if self.supplier:
            parts.append(self.supplier)
        parts.append(
            f"{cur}{_qty(self.price_per_bag)} / {ui('bag')} "
            f"({cur}{_qty(self.unit_cost.normalize())} / {unit or ui('unit')})"
        )
        parts.append(ui("{qty} left", qty=f"{_qty(self.left_qty)} {unit}".strip()))
        return " · ".join(parts)


class FieldOperationInput(TimeStampedModel):
    """One chemical used in a field-work entry: dosage, quantity, and the purchase whose price it is costed at."""

    operation = models.ForeignKey(
        FieldOperation, verbose_name="Field work", on_delete=models.CASCADE, related_name="chemicals"
    )
    agri_input = models.ForeignKey(
        AgriInput, verbose_name="Fertilizer / Chemical", on_delete=models.PROTECT, related_name="uses"
    )
    purchase = models.ForeignKey(
        AgriInputPurchase,
        verbose_name="From purchase",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="uses",
        help_text="Its price is used. Deleting the purchase keeps this price.",
    )
    dose_qty = models.DecimalField("Dosage", max_digits=12, decimal_places=3, default=0, validators=POSITIVE)
    dose_basis = models.CharField("Dosage is", max_length=10, choices=LINE_BASES, default="water")
    dose_water = models.DecimalField(
        "For litres of water", max_digits=10, decimal_places=2, default=0, validators=POSITIVE
    )
    quantity = models.DecimalField("Quantity used", max_digits=14, decimal_places=3, default=0, validators=POSITIVE)
    unit_cost = models.DecimalField("Cost per unit", max_digits=14, decimal_places=4, default=0, validators=POSITIVE)
    cost = money_field("Cost", editable=False)

    class Meta:
        ordering = ["operation__date", "id"]
        verbose_name = "Chemical used"
        verbose_name_plural = "Chemicals used (field work)"

    def __str__(self):
        return f"{self.agri_input.name} {_qty(self.quantity)} {self.agri_input.unit_name}".strip()

    @property
    def date(self):
        return self.operation.date

    def dosage_text(self):
        return dosage_text(self.dose_qty, self.dose_basis, self.dose_water, self.agri_input.unit_name)

    def save(self, *args, **kwargs):
        using = kwargs.get("using") or self._state.db or "default"
        if not self.unit_cost:
            if self.purchase_id:
                self.unit_cost = self.purchase.unit_cost
            elif self.agri_input_id:
                self.unit_cost = self.agri_input.unit_cost
        if not self.quantity and self.operation_id:
            self.quantity = self.operation.chemical_qty(self.dose_qty, self.dose_basis, self.dose_water)
        self.cost = q2(Decimal(self.quantity or 0) * Decimal(self.unit_cost or 0))
        before = None
        if self.pk:
            before = type(self).objects.using(using).filter(pk=self.pk).values_list("purchase", "agri_input").first()
        super().save(*args, **kwargs)
        _refresh_after_line(self, using, before)
        self.operation.update_costs(using)


def _refresh_after_line(line, using, before=None):
    """Purchase 'used / left' and the chemical's stock, for this line's purchase and chemical (and the ones
    it had before an edit)."""
    purchases = {line.purchase_id} | ({before[0]} if before else set())
    inputs = {line.agri_input_id} | ({before[1]} if before else set())
    for p in AgriInputPurchase.objects.using(using).filter(pk__in=[x for x in purchases if x]):
        p.refresh_used(using)
    for a in AgriInput.objects.using(using).filter(pk__in=[x for x in inputs if x]):
        a.refresh_stock(using)


def _line_deleted(sender, instance, using, **kwargs):
    _refresh_after_line(instance, using)
    op = FieldOperation.objects.using(using).filter(pk=instance.operation_id).first()
    if op is not None:  # not when the whole field-work entry is being deleted
        op.update_costs(using)


def _purchase_deleted(sender, instance, using, **kwargs):
    a = AgriInput.objects.using(using).filter(pk=instance.agri_input_id).first()
    if a is not None:
        a.refresh_stock(using)


models.signals.post_delete.connect(_line_deleted, sender=FieldOperationInput, dispatch_uid="pms_line_deleted")
models.signals.post_delete.connect(_purchase_deleted, sender=AgriInputPurchase, dispatch_uid="pms_purchase_deleted")


def calc_field(verbose_name, help_text="", places=3, digits=12):
    return models.DecimalField(
        verbose_name, max_digits=digits, decimal_places=places, default=0, editable=False, help_text=help_text
    )


class Harvest(RefreshHarvests, TimeStampedModel):
    """Module 7 - Product details: raw produce collected from the farm. How much one person picked is
    worked out from the people of the same date and job (see estate/productivity.py)."""

    date = models.DateField("Date", default=timezone.localdate)
    product = models.ForeignKey(Product, verbose_name="Product", on_delete=models.PROTECT, related_name="harvests")
    job_type = models.ForeignKey(
        JobType,
        verbose_name="Picking / harvesting job",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Leave blank to use the product's picking job. "
        "The people who did this job on the same date are fetched automatically.",
    )
    block = models.ForeignKey(Block, verbose_name="Block / Plot", null=True, blank=True, on_delete=models.SET_NULL)
    harvest_round = models.CharField("Harvest round", max_length=30, blank=True, help_text="e.g. Round 3")
    quantity = qty_field("Quantity (raw)", help_text="Total harvested")
    quality_notes = models.CharField("Quality / Condition", max_length=120, blank=True)
    remarks = models.TextField("Comments", blank=True)
    # --- worked out automatically (same date and job) ---
    ind_people = calc_field("People - individual", "Work Details - Individual; half day = 0.5", 1, 8)
    ind_ot_people = calc_field("OT people - individual", places=1, digits=8)
    ind_ot_hours = calc_field("OT hours - individual", places=2, digits=9)
    count_people = calc_field("People - labour count", "Daily Labour Count", 1, 8)
    count_ot_people = calc_field("OT people - labour count", places=1, digits=8)
    count_ot_hours = calc_field("OT hours - labour count", places=2, digits=9)
    total_hours = calc_field("Total hours worked", "People x hours in a full day + OT hours", 2, 10)
    per_hour = calc_field("Harvest per hour", "Day's quantity / total hours (same for full day and OT hours)")
    full_day_quantity = calc_field("Harvest in full days (all people)", "Per hour x people x hours in a full day")
    ot_quantity = calc_field("Harvest in OT (all people)", "Per hour x OT hours")
    per_person_full_day = calc_field("1 person - full day", "Per hour x hours in a full day")
    per_person_ot = calc_field("1 person - OT", "Per hour x average OT hours of the people who did OT")
    per_person_full_day_ot = calc_field(
        "1 person - full day + OT", "Per hour x (full day + average OT hours of the people who did OT)"
    )
    per_person_average = calc_field("1 person - average", "Day's quantity / people")

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [models.Index(fields=["date"]), models.Index(fields=["date", "job_type"])]
        verbose_name = "Harvest"
        verbose_name_plural = "Harvests"

    def __str__(self):
        return f"{self.date:%d/%m/%y} - {self.product} - {self.quantity}"

    def save(self, *args, **kwargs):
        if not self.job_type_id and self.product_id:
            self.job_type_id = self.product.harvest_job_type_id
        super().save(*args, **kwargs)  # also works out the per-person figures


class ProcessingBatch(TimeStampedModel):
    """Module 8 - Processed product details (drying / curing / pulping / grading)."""

    batch_no = models.CharField("Batch no.", max_length=30, blank=True, help_text="Leave blank to number automatically")
    date = models.DateField("Date", default=timezone.localdate)
    product = models.ForeignKey(
        Product, verbose_name="Product", on_delete=models.PROTECT, related_name="processing_batches"
    )
    process_type = models.ForeignKey(ProcessType, verbose_name="Process", on_delete=models.PROTECT)
    input_quantity = qty_field("Total quantity (input, raw)")
    output_quantity = qty_field("Processed quantity (output)")
    conversion_ratio = models.DecimalField(
        "Ratio (input / output)", max_digits=8, decimal_places=3, default=0, editable=False
    )
    recovery_percent = models.DecimalField("Recovery %", max_digits=6, decimal_places=2, default=0, editable=False)
    output_grade = models.CharField(
        "Output grade", max_length=60, blank=True, help_text="e.g. 8 mm bold, 7-8 mm, Rejects"
    )
    fuel_type = models.CharField("Fuel used", max_length=40, blank=True, help_text="e.g. Firewood, Diesel, Electricity")
    processing_cost = money_field(
        "Processing cost (fuel + labour) / KG", help_text="Cost for each kg of raw input; the batch total is calculated"
    )
    total_cost = money_field("Total processing cost (batch)", editable=False)
    remarks = models.TextField("Comments", blank=True)

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [models.Index(fields=["date"])]
        verbose_name = "Processing batch"
        verbose_name_plural = "Processing batches"

    def __str__(self):
        return f"{self.batch_no or self.pk} - {self.product} ({self.date:%d/%m/%y})"

    def save(self, *args, **kwargs):
        inp, out = Decimal(self.input_quantity or 0), Decimal(self.output_quantity or 0)
        self.conversion_ratio = (inp / out).quantize(Decimal("0.001")) if out else ZERO
        self.recovery_percent = q2(out * 100 / inp) if inp else ZERO
        self.total_cost = q2(inp * Decimal(self.processing_cost or 0))  # e.g. 100 kg x 11 = 1,100
        super().save(*args, **kwargs)
        if not self.batch_no:
            self.batch_no = f"{appsettings.current().batch_no_prefix}{self.date:%y%m}-{self.pk:04d}"
            type(self).objects.filter(pk=self.pk).update(batch_no=self.batch_no)


class Expense(TimeStampedModel):
    date = models.DateField("Date", default=timezone.localdate)
    category = models.ForeignKey(ExpenseCategory, verbose_name="Category", on_delete=models.PROTECT)
    description = models.CharField("Description", max_length=200)
    block = models.ForeignKey(Block, verbose_name="Block / Plot", null=True, blank=True, on_delete=models.SET_NULL)
    vendor = models.CharField("Vendor / Paid to", max_length=120, blank=True)
    amount = money_field("Amount")
    bill_no = models.CharField("Bill / Voucher no.", max_length=40, blank=True)
    remarks = models.TextField("Comments", blank=True)

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [models.Index(fields=["date"])]
        verbose_name = "Expense"
        verbose_name_plural = "Expenses"

    def __str__(self):
        return f"{self.date:%d/%m/%y} - {self.description} - {self.amount}"


class Sale(TimeStampedModel):
    """Produce sold. The money comes in one or more payments (SaleReceipt): 30 % first, 20 % later ..."""

    date = models.DateField("Sold date", default=timezone.localdate)
    product = models.ForeignKey(Product, verbose_name="Product", on_delete=models.PROTECT, related_name="sales")
    grade = models.CharField("Grade", max_length=60, blank=True)
    quantity = qty_field("Quantity sold")
    rate = money_field("Rate per unit", help_text="Filled from the product's usual sale rate. You can change it.")
    total_amount = money_field("Total amount", editable=False)
    buyer = models.CharField("Buyer / Auction centre", max_length=120, blank=True)
    invoice_no = models.CharField("Invoice / Lot no.", max_length=40, blank=True)
    payment_status = models.ForeignKey(
        ReceiptStatus,
        verbose_name="Payment status",
        null=True,
        blank=True,
        editable=False,  # set from the payments received
        on_delete=models.SET_NULL,
    )
    remarks = models.TextField("Comments", blank=True)
    # --- the money received: one SaleReceipt row per payment ---
    amount_received = money_field("Amount received", editable=False)
    balance = money_field("Balance (to receive)", editable=False)
    receipt_state = models.CharField(
        "Payment", max_length=10, choices=RECEIPT_STATES, default="pending", editable=False, db_index=True
    )
    last_received_on = models.DateField("Payment received date (last)", null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [models.Index(fields=["date"])]
        verbose_name = "Sale"
        verbose_name_plural = "Sales"

    def __str__(self):
        return f"{self.date:%d/%m/%y} - {self.product} - {self.quantity}"

    def clean(self):
        if self.pk and self.quantity is not None and self.rate is not None:
            received = self.receipts.aggregate(s=models.Sum("amount"))["s"] or 0
            if q2(self.quantity * self.rate) < received:
                raise ValidationError(
                    ui(
                        "{amount} is already received for this sale: the total can't be less.",
                        amount=f"{received:,.2f}",
                    )
                )

    def save(self, *args, **kwargs):
        if not self.rate and self.product_id:
            self.rate = self.product.typical_rate
        self.total_amount = q2((self.quantity or 0) * (self.rate or 0))
        if self.pk:
            agg = self.receipts.aggregate(s=models.Sum("amount"), d=models.Max("date"))
            self.amount_received, self.last_received_on = agg["s"] or 0, agg["d"]
        self._set_state()
        super().save(*args, **kwargs)

    def _set_state(self):
        self.balance = q2(self.total_amount - (self.amount_received or 0))
        if self.amount_received and self.balance <= 0:
            self.receipt_state = "received"
        elif self.amount_received:
            self.receipt_state = "part"
        else:
            self.receipt_state = "pending"
        status = (
            ReceiptStatus.objects.filter(means=self.receipt_state).order_by("-is_active", "sort_order", "id").first()
        )
        if status:
            self.payment_status = status

    def refresh_received(self):
        """After a payment received was added, changed or deleted: totals, balance after each, status."""
        running = Decimal(0)
        receipts = list(self.receipts.order_by("date", "id"))
        for r in receipts:
            running += r.amount
            after = q2(self.total_amount - running)
            if r.balance_after != after:
                SaleReceipt.objects.filter(pk=r.pk).update(balance_after=after)
        self.amount_received = running
        self.last_received_on = receipts[-1].date if receipts else None
        self._set_state()
        type(self).objects.filter(pk=self.pk).update(
            amount_received=self.amount_received,
            last_received_on=self.last_received_on,
            balance=self.balance,
            receipt_state=self.receipt_state,
            payment_status=self.payment_status,
        )

    @property
    def received_percent(self):
        return q2(self.amount_received * 100 / self.total_amount) if self.total_amount else Decimal(0)


class SaleReceipt(TimeStampedModel):
    """One payment received for a sale: 30 % on 05/10, 20 % on 12/10, the rest on 13/10 ..."""

    sale = models.ForeignKey(Sale, verbose_name="Sale", on_delete=models.PROTECT, related_name="receipts")
    date = models.DateField("Payment received date", default=timezone.localdate)
    amount = money_field("Amount received")
    payment_mode = models.ForeignKey(
        PaymentMode, verbose_name="Payment mode", null=True, blank=True, on_delete=models.SET_NULL
    )
    balance_after = money_field("Balance after this payment", editable=False)
    remarks = models.CharField("Remarks", max_length=200, blank=True, help_text="e.g. cheque / UTR no.")

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [models.Index(fields=["date"])]
        verbose_name = "Sale payment received"
        verbose_name_plural = "Sale payments received"

    def __str__(self):
        return f"{self.date:%d/%m/%y} - {self.sale} - {self.amount}"

    @property
    def percent(self):
        """Share of the sale's total amount."""
        total = self.sale.total_amount
        return q2(self.amount * 100 / total) if total else Decimal(0)

    def clean(self):
        if self.amount is not None and self.amount <= 0:
            raise ValidationError({"amount": ui("Enter the amount received.")})
        if self.sale_id and self.amount:
            if self.sale.date and self.date and self.date < self.sale.date:
                raise ValidationError({"date": ui("The payment can't be received before the sold date.")})
            other = self.sale.receipts.exclude(pk=self.pk).aggregate(s=models.Sum("amount"))["s"] or 0
            due = self.sale.total_amount - other
            if self.amount > due:
                raise ValidationError(
                    {"amount": ui("Only {due} is still to be received for this sale.", due=f"{max(due, 0):,.2f}")}
                )

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.sale.refresh_received()

    def delete(self, *args, **kwargs):
        sale = self.sale
        result = super().delete(*args, **kwargs)
        sale.refresh_received()
        return result


class SavedReport(TimeStampedModel):
    name = models.CharField("Report name", max_length=120)
    dataset = models.CharField("Data source", max_length=40)
    params = models.JSONField("Parameters", default=dict)
    description = models.CharField("Description", max_length=250, blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Saved report"
        verbose_name_plural = "Saved reports"

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("reports:builder") + f"?saved={self.pk}"
