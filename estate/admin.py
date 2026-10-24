from django.contrib import admin

from . import models as m
from .registry import MODULES

admin.site.site_header = "Plantation Management System - Admin"
admin.site.site_title = "PMS Admin"

for cfg in MODULES:
    attrs = {
        "list_display": cfg.list_fields,
        "search_fields": cfg.search_fields,
        "list_filter": cfg.filter_fields,
        "list_per_page": 50,
    }
    if cfg.date_field:
        attrs["date_hierarchy"] = cfg.date_field
    admin.site.register(cfg.model, type(f"{cfg.model.__name__}Admin", (admin.ModelAdmin,), attrs))

admin.site.register(m.SavedReport)
