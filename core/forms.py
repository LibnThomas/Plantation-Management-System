"""Forms of the App Settings page (one per tab)."""

import io

from django import forms
from django.conf import settings

from estate.forms import DateInput, StyledModelForm, style_fields

from . import appsettings, licence
from .i18n import ui
from .models import AppSettings

LOGO_MAX_BYTES = 500 * 1024
LOGO_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


class GeneralForm(StyledModelForm):
    logo_file = forms.FileField(required=False)
    remove_logo = forms.BooleanField(required=False)

    class Meta:
        model = AppSettings
        fields = [
            "estate_name",
            "address",
            "phone",
            "email",
            "currency_symbol",
            "financial_year_start_month",
            "crop_season_start_month",
            "employee_code_prefix",
            "batch_no_prefix",
            "work_hours_per_day",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["logo_file"].label = ui("Logo (PNG / JPG, up to 500 KB)")
        self.fields["logo_file"].help_text = ui("Shown in the top bar, on the login page and on PDF reports")
        self.fields["remove_logo"].label = ui("Remove the current logo")
        self.fields["logo_file"].widget.attrs["accept"] = "image/png,image/jpeg,image/webp"
        style_fields({k: self.fields[k] for k in ("logo_file", "remove_logo")})

    def clean_logo_file(self):
        upload = self.cleaned_data.get("logo_file")
        if not upload:
            return None
        if upload.size > LOGO_MAX_BYTES:
            raise forms.ValidationError(ui("The logo is too big (maximum 500 KB)."))
        data = upload.read()
        try:
            from PIL import Image

            with Image.open(io.BytesIO(data)) as img:
                img.verify()
                kind = img.format
        except Exception:
            raise forms.ValidationError(ui("This file is not a PNG, JPG or WEBP picture.")) from None
        if kind not in LOGO_TYPES:
            raise forms.ValidationError(ui("This file is not a PNG, JPG or WEBP picture."))
        return data, LOGO_TYPES[kind]

    def save(self, commit=True):
        obj = super().save(commit=False)
        if self.cleaned_data.get("remove_logo"):
            obj.logo, obj.logo_type = None, ""
        elif self.cleaned_data.get("logo_file"):
            obj.logo, obj.logo_type = self.cleaned_data["logo_file"]
        if commit:
            obj.save()
        return obj


class ColorInput(forms.TextInput):
    input_type = "color"


class ThemeForm(StyledModelForm):
    class Meta:
        model = AppSettings
        fields = ["theme_preset", "primary_color", "header_color", "background_color", "font", "base_font_size"]
        widgets = {"primary_color": ColorInput(), "header_color": ColorInput(), "background_color": ColorInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("primary_color", "header_color", "background_color"):
            self.fields[name].widget.attrs["class"] = "form-control form-control-color"
        self.fields["base_font_size"].widget.attrs.update(min=12, max=20)

    def clean(self):
        data = super().clean()
        # white text sits on the main and top bar colours; dark text on the page background
        for name in ("primary_color", "header_color"):
            color = data.get(name)
            if color and appsettings.contrast(color, "#ffffff") < 3:
                self.add_error(name, ui("This colour is too light: white text on it would be hard to read."))
        bg = data.get("background_color")
        if bg and appsettings.contrast(bg, "#1d2b22") < 7:
            self.add_error("background_color", ui("This colour is too dark for a page background."))
        return data


class SecurityForm(StyledModelForm):
    class Meta:
        model = AppSettings
        fields = ["session_timeout_minutes"]


class LicenceForm(forms.Form):
    licence_key = forms.CharField(widget=forms.Textarea(attrs={"rows": 3, "class": "form-control licence-key"}))

    def __init__(self, *args, install_id="", **kwargs):
        super().__init__(*args, **kwargs)
        self.install_id = install_id
        self.fields["licence_key"].label = ui("New licence key")
        self.payload = None

    def clean_licence_key(self):
        key = "".join(self.cleaned_data["licence_key"].split())
        try:
            self.payload = licence.decode(key, self.install_id)
        except licence.LicenceError as e:
            raise forms.ValidationError(ui(str(e))) from None
        return key


class SubscriptionForm(StyledModelForm):
    """Valid till date: typed by the administrator (API off) or fetched from the API (API on)."""

    class Meta:
        model = AppSettings
        fields = [
            "sub_api_enabled",
            "sub_valid_until",
            "sub_max_users",
            "sub_api_url",
            "sub_api_token",
            "sub_api_field",
            "sub_api_interval_hours",
        ]
        widgets = {"sub_valid_until": DateInput(), "sub_api_token": forms.PasswordInput(render_value=False)}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["sub_api_token"].help_text = ui(
            "Sent as 'Authorization: Bearer …'. Leave blank to keep the saved one."
        )
        self.fields["sub_api_token"].widget.attrs["autocomplete"] = "new-password"
        self.fields["sub_max_users"].required = False
        self.locked = not settings.SUBSCRIPTION_MANUAL_ALLOWED
        if self.locked:  # vendor-controlled installation: only the API sets the date
            for name in self.fields:
                self.fields[name].disabled = True

    def clean_sub_max_users(self):
        return self.cleaned_data.get("sub_max_users") or 0

    def clean_sub_api_token(self):
        return self.cleaned_data.get("sub_api_token") or self.instance.sub_api_token

    def clean(self):
        data = super().clean()
        if data.get("sub_api_enabled") and not self.locked:
            if not data.get("sub_api_url") and not settings.SUBSCRIPTION_API_URL:
                self.add_error("sub_api_url", ui("Enter the API URL, or switch the API off."))
            # the API owns these values: what was typed is ignored
            data["sub_valid_until"] = self.instance.sub_valid_until
            data["sub_max_users"] = self.instance.sub_max_users
        return data
