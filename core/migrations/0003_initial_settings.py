"""
Starting values for App Settings: fonts, colour themes and the settings row itself.
This is the only place these values are written; afterwards they are ordinary data.
The estate name, currency, FY / season months and number prefixes start from .env (if set).
"""

from django.conf import settings
from django.db import migrations

FONTS = [
    # name, CSS font list
    ("System default", 'system-ui, -apple-system, "Segoe UI", Roboto, Arial'),
    ("Noto Sans", '"PMS Noto Sans", "Noto Sans", Arial'),
    ("Arial / Helvetica", "Arial, Helvetica"),
    ("Verdana (wide, easy to read)", "Verdana, Geneva, Tahoma"),
    ("Georgia (serif)", "Georgia, 'Times New Roman', serif"),
    ("Trebuchet", '"Trebuchet MS", "Lucida Grande", Arial'),
]

THEMES = [
    # English, Malayalam, Hindi, Tamil, main colour, top bar, page background
    ("Estate green", "എസ്റ്റേറ്റ് പച്ച", "बागान हरा", "தோட்டப் பச்சை", "#1f5f3f", "#174a31", "#f4f6f4"),
    ("Cardamom", "ഏലം", "इलायची", "ஏலக்காய்", "#4d7c0f", "#3f6212", "#f6f8f1"),
    ("Monsoon blue", "മൺസൂൺ നീല", "मानसून नीला", "பருவமழை நீலம்", "#1d4e89", "#163c69", "#f3f6fa"),
    ("Coffee brown", "കാപ്പി ബ്രൗൺ", "कॉफ़ी भूरा", "காபி பழுப்பு", "#6b4226", "#4e2f1b", "#f8f5f2"),
    ("Pepper red", "കുരുമുളക് ചുവപ്പ്", "काली मिर्च लाल", "மிளகு சிவப்பு", "#9b2c2c", "#7a2121", "#faf4f4"),
    ("Slate", "സ്ലേറ്റ്", "स्लेट", "சாம்பல்", "#334155", "#1e293b", "#f5f6f8"),
]


def load_initial_settings(apps, schema_editor):
    db = schema_editor.connection.alias
    Font = apps.get_model("core", "FontChoice")
    Theme = apps.get_model("core", "ThemePreset")
    AppSettings = apps.get_model("core", "AppSettings")
    fonts = [Font.objects.using(db).get_or_create(name=n, defaults={"css_stack": css, "sort_order": i})[0]
             for i, (n, css) in enumerate(FONTS)]
    themes = [
        Theme.objects.using(db).get_or_create(name=n, defaults={
            "name_ml": ml, "name_hi": hi, "name_ta": ta, "primary_color": p, "header_color": h,
            "background_color": bg, "sort_order": i})[0]
        for i, (n, ml, hi, ta, p, h, bg) in enumerate(THEMES)
    ]
    AppSettings.objects.using(db).get_or_create(pk=1, defaults={
        "estate_name": getattr(settings, "ESTATE_NAME", "My Cardamom Estate"),
        "currency_symbol": getattr(settings, "CURRENCY_SYMBOL", "₹"),
        "financial_year_start_month": getattr(settings, "FINANCIAL_YEAR_START_MONTH", 4),
        "crop_season_start_month": getattr(settings, "CROP_SEASON_START_MONTH", 8),
        "employee_code_prefix": getattr(settings, "EMPLOYEE_CODE_PREFIX", "EMP"),
        "batch_no_prefix": getattr(settings, "BATCH_NO_PREFIX", "PB"),
        "theme_preset": themes[0], "font": fonts[0],
        "primary_color": themes[0].primary_color, "header_color": themes[0].header_color,
        "background_color": themes[0].background_color,
    })


class Migration(migrations.Migration):
    dependencies = [("core", "0002_app_settings")]
    operations = [migrations.RunPython(load_initial_settings, migrations.RunPython.noop)]
