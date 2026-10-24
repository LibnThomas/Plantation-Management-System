from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models

LANGUAGE_CODES = ("en", "ml", "hi", "ta")


class Label(models.Model):
    """
    Every text shown in the app (field names, menu items, buttons, messages) in four languages.
    The customer can rename anything by editing `en` (or any language); `default` keeps the
    original wording so it can be restored.
    """

    key = models.CharField("Key", max_length=255, unique=True)
    group = models.CharField("Group", max_length=60, db_index=True)
    default = models.TextField("Original text")
    en = models.TextField("English", blank=True)
    ml = models.TextField("Malayalam", blank=True)
    hi = models.TextField("Hindi", blank=True)
    ta = models.TextField("Tamil", blank=True)
    updated_at = models.DateTimeField("Updated at", auto_now=True)

    class Meta:
        ordering = ["group", "key"]
        indexes = [models.Index(fields=["updated_at"])]
        verbose_name = "Label"

    def __str__(self):
        return self.en or self.default

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        from . import i18n

        i18n.recheck()  # this worker shows the change on its next page

    def delete(self, *args, **kwargs):
        result = super().delete(*args, **kwargs)
        from . import i18n

        i18n.recheck()
        return result


class UserPreference(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="pms_preference")
    language = models.CharField("Language", max_length=5, default="en")
    text_size = models.PositiveSmallIntegerField("Text size (%)", default=100)

    def __str__(self):
        return f"{self.user} ({self.language})"


# ---------------------------------------------------------------------------
# App Settings (admin only): estate profile, theme, security, subscription
# ---------------------------------------------------------------------------
HEX_COLOR = RegexValidator(r"^#[0-9a-fA-F]{6}$", "Use a colour like #1f5f3f")
MONTH = [MinValueValidator(1), MaxValueValidator(12)]


class FontChoice(models.Model):
    """Fonts the admin can pick for the app (starting list comes from a migration)."""

    name = models.CharField("Font", max_length=60, unique=True)
    css_stack = models.CharField("CSS font list", max_length=200)
    sort_order = models.PositiveSmallIntegerField("Order", default=0)
    is_active = models.BooleanField("Active", default=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "Font"

    def __str__(self):
        return self.name


class ThemePreset(models.Model):
    """Ready-made colour sets (starting list comes from a migration)."""

    name = models.CharField("Theme", max_length=60, unique=True)
    name_ml = models.CharField("Name (Malayalam)", max_length=60, blank=True)
    name_hi = models.CharField("Name (Hindi)", max_length=60, blank=True)
    name_ta = models.CharField("Name (Tamil)", max_length=60, blank=True)
    primary_color = models.CharField("Main colour", max_length=7, validators=[HEX_COLOR])
    header_color = models.CharField("Top bar colour", max_length=7, validators=[HEX_COLOR])
    background_color = models.CharField("Page background", max_length=7, validators=[HEX_COLOR])
    sort_order = models.PositiveSmallIntegerField("Order", default=0)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "Theme preset"

    def __str__(self):
        from django.utils import translation

        lang = (translation.get_language() or "en")[:2]
        return (getattr(self, f"name_{lang}", "") if lang != "en" else "") or self.name


class AppSettings(models.Model):
    """One row (id = 1) with the settings of this installation. Shared by real and demo data."""

    # estate profile
    estate_name = models.CharField("Estate name", max_length=120, default="My Cardamom Estate")
    address = models.TextField("Address", blank=True)
    phone = models.CharField("Phone", max_length=40, blank=True)
    email = models.EmailField("Email", blank=True)
    logo = models.BinaryField("Logo", blank=True, null=True, editable=False)
    logo_type = models.CharField(max_length=40, blank=True, editable=False)
    currency_symbol = models.CharField("Currency symbol", max_length=5, default="₹")
    financial_year_start_month = models.PositiveSmallIntegerField(
        "Financial year starts in month", default=4, validators=MONTH, help_text="1 = January ... 12 = December"
    )
    crop_season_start_month = models.PositiveSmallIntegerField(
        "Crop season starts in month", default=8, validators=MONTH, help_text="1 = January ... 12 = December"
    )
    employee_code_prefix = models.CharField("Employee code prefix", max_length=8, default="EMP")
    batch_no_prefix = models.CharField("Processing batch prefix", max_length=8, default="PB")
    work_hours_per_day = models.DecimalField(
        "Hours in a full working day",
        max_digits=4,
        decimal_places=2,
        default=8,
        validators=[MinValueValidator(1), MaxValueValidator(24)],
        help_text="Used to work out harvest per person and per hour (OT is counted in hours)",
    )
    # theme
    theme_preset = models.ForeignKey(
        ThemePreset, verbose_name="Theme", null=True, blank=True, on_delete=models.SET_NULL
    )
    primary_color = models.CharField("Main colour", max_length=7, default="#1f5f3f", validators=[HEX_COLOR])
    header_color = models.CharField("Top bar colour", max_length=7, default="#174a31", validators=[HEX_COLOR])
    background_color = models.CharField("Page background", max_length=7, default="#f4f6f4", validators=[HEX_COLOR])
    font = models.ForeignKey(FontChoice, verbose_name="Font", null=True, blank=True, on_delete=models.SET_NULL)
    base_font_size = models.PositiveSmallIntegerField(
        "Text size (px)", default=15, validators=[MinValueValidator(12), MaxValueValidator(20)]
    )
    # security
    session_timeout_minutes = models.PositiveIntegerField(
        "Log out after inactivity (minutes)", default=0, help_text="0 = stay logged in"
    )
    # subscription (filled by the system / the licence key, not typed by the customer)
    install_id = models.CharField("Installation ID", max_length=20, blank=True, editable=False)
    first_run_date = models.DateField("First used on", null=True, blank=True, editable=False)
    last_seen_date = models.DateField(null=True, blank=True, editable=False)
    licence_key = models.TextField("Licence key", blank=True, editable=False)
    # the date access is paid until: typed by the administrator, or fetched from the API when it is on
    sub_valid_until = models.DateField(
        "Valid till", null=True, blank=True, help_text="Leave blank to use the free trial"
    )
    sub_max_users = models.PositiveIntegerField("Users allowed", default=0, help_text="0 = no limit")
    sub_plan = models.CharField("Plan", max_length=60, blank=True)
    sub_api_enabled = models.BooleanField(
        "Update the valid till date from the API",
        default=False,
        help_text="On: the date comes from the API URL when the app starts and every few hours. "
        "Off: the administrator types it here.",
    )
    sub_api_url = models.URLField(
        "API URL", max_length=300, blank=True, help_text="Called with ?install_id=… ; must return the valid till date"
    )
    sub_api_token = models.CharField("API token (optional)", max_length=200, blank=True)
    sub_api_field = models.CharField(
        "Date field in the answer",
        max_length=60,
        default="valid_until",
        help_text="Name of the JSON field with the date, e.g. valid_until or data.valid_until",
    )
    sub_api_interval_hours = models.PositiveSmallIntegerField(
        "Check again every (hours)", default=12, validators=[MinValueValidator(1), MaxValueValidator(168)]
    )
    sub_api_checked_at = models.DateTimeField("Last API check", null=True, blank=True, editable=False)
    sub_api_ok_at = models.DateTimeField("Last successful API check", null=True, blank=True, editable=False)
    sub_api_message = models.CharField("Last API result", max_length=300, blank=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "App settings"
        verbose_name_plural = "App settings"

    def __str__(self):
        return self.estate_name

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class ActivityLog(models.Model):
    """Who added, changed or deleted what (admin can review it in App Settings)."""

    at = models.DateTimeField("When", auto_now_add=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="User", null=True, blank=True, on_delete=models.SET_NULL
    )
    action = models.CharField("Action", max_length=10)  # add / change / delete
    model = models.CharField("Record type", max_length=60)
    object_id = models.CharField("Record ID", max_length=40)
    summary = models.CharField("Record", max_length=200)
    demo = models.BooleanField("In demo mode", default=False)

    class Meta:
        ordering = ["-at"]
        indexes = [models.Index(fields=["user", "-at"])]
        verbose_name = "Activity"
        verbose_name_plural = "Activity log"

    def __str__(self):
        return f"{self.at:%d/%m/%y %H:%M} {self.user} {self.action} {self.summary}"
