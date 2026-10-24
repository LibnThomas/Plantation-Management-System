"""Drop-down lists. All values are rows in these tables (initial rows come from migration 0002)."""

from django.db import models

from .base import Lookup


class Gender(Lookup):
    class Meta(Lookup.Meta):
        verbose_name = "Gender"
        verbose_name_plural = "Genders"


class EmploymentType(Lookup):
    class Meta(Lookup.Meta):
        verbose_name = "Employment type"
        verbose_name_plural = "Employment types"


class JobCategory(Lookup):
    class Meta(Lookup.Meta):
        verbose_name = "Job category"
        verbose_name_plural = "Job categories"


class CropRole(Lookup):
    class Meta(Lookup.Meta):
        verbose_name = "Crop role"
        verbose_name_plural = "Crop roles"


class Unit(Lookup):
    class Meta(Lookup.Meta):
        verbose_name = "Unit"
        verbose_name_plural = "Units"


class InputType(Lookup):
    class Meta(Lookup.Meta):
        verbose_name = "Fertilizer / chemical type"
        verbose_name_plural = "Fertilizer / chemical types"


class AttendanceStatus(Lookup):
    pay_fraction = models.DecimalField(
        "Paid part of a day",
        max_digits=3,
        decimal_places=2,
        default=0,
        help_text="1 = full day's wage, 0.5 = half, 0 = not paid",
    )
    is_leave = models.BooleanField("Counts as leave", default=False)
    color = models.CharField("Colour", max_length=7, default="#6c757d", help_text="Button colour, e.g. #198754")

    class Meta(Lookup.Meta):
        verbose_name = "Attendance status"
        verbose_name_plural = "Attendance statuses"


class PaymentMode(Lookup):
    class Meta(Lookup.Meta):
        verbose_name = "Payment mode"
        verbose_name_plural = "Payment modes"


class PaymentStatus(Lookup):
    is_final = models.BooleanField(
        "Paid / closed",
        default=False,
        help_text="Payments with this status are never re-calculated by Generate Payroll",
    )

    class Meta(Lookup.Meta):
        verbose_name = "Salary payment status"
        verbose_name_plural = "Salary payment statuses"


class OperationType(Lookup):
    input_type = models.ForeignKey(
        InputType,
        verbose_name="Usual fertilizer / chemical type",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text="Narrows the fertilizer / chemical list for this operation",
    )

    class Meta(Lookup.Meta):
        verbose_name = "Field operation"
        verbose_name_plural = "Field operations"


class ApplicationMethod(Lookup):
    class Meta(Lookup.Meta):
        verbose_name = "Application method"
        verbose_name_plural = "Application methods"


class ProcessType(Lookup):
    class Meta(Lookup.Meta):
        verbose_name = "Process type"
        verbose_name_plural = "Process types"


class ExpenseCategory(Lookup):
    class Meta(Lookup.Meta):
        verbose_name = "Expense category"
        verbose_name_plural = "Expense categories"


# what a sale payment status means: set on the sale automatically from the payments received
RECEIPT_STATES = [("pending", "Pending"), ("part", "Partially received"), ("received", "Received")]


class ReceiptStatus(Lookup):
    means = models.CharField(
        "Means",
        max_length=10,
        choices=RECEIPT_STATES,
        blank=True,
        help_text="Sales get this status automatically when their payments received match it",
    )

    class Meta(Lookup.Meta):
        verbose_name = "Sale payment status"
        verbose_name_plural = "Sale payment statuses"
