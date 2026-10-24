from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import translation

ZERO = Decimal("0")
POSITIVE = [MinValueValidator(0)]


def q2(value):
    """Round to 2 decimal places (money)."""
    return Decimal(value or 0).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def money_field(verbose_name, **kw):
    kw.setdefault("max_digits", 12)
    kw.setdefault("decimal_places", 2)
    kw.setdefault("default", 0)
    kw.setdefault("validators", POSITIVE)
    return models.DecimalField(verbose_name, **kw)


def qty_field(verbose_name, **kw):
    kw.setdefault("max_digits", 12)
    kw.setdefault("decimal_places", 3)
    kw.setdefault("default", 0)
    kw.setdefault("validators", POSITIVE)
    return models.DecimalField(verbose_name, **kw)


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField("Created at", auto_now_add=True)
    updated_at = models.DateTimeField("Updated at", auto_now=True)
    # db_constraint=False: users are shared between the real and the demo database
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Created by",
        null=True,
        blank=True,
        db_constraint=False,
        db_index=False,
        on_delete=models.DO_NOTHING,
        related_name="+",
        editable=False,
    )

    class Meta:
        abstract = True


class LocalizedNameMixin(models.Model):
    """A `name` plus its Malayalam / Hindi / Tamil versions; str() shows the user's language."""

    name_ml = models.CharField("Name (Malayalam)", max_length=150, blank=True)
    name_hi = models.CharField("Name (Hindi)", max_length=150, blank=True)
    name_ta = models.CharField("Name (Tamil)", max_length=150, blank=True)

    class Meta:
        abstract = True

    def __str__(self):
        return self.display_name

    @property
    def display_name(self):
        lang = (translation.get_language() or "en")[:2]
        return (getattr(self, f"name_{lang}", "") if lang != "en" else "") or self.name


class Lookup(LocalizedNameMixin, TimeStampedModel):
    """Base for every drop-down list. Values are data (initial ones come from a migration)."""

    name = models.CharField("Name", max_length=100, unique=True)
    sort_order = models.PositiveIntegerField("Sort order", default=0)
    is_active = models.BooleanField("Active", default=True)

    class Meta:
        abstract = True
        ordering = ["sort_order", "name"]

    @classmethod
    def first_active(cls):
        return cls.objects.filter(is_active=True).order_by("sort_order", "id").first()
