from django.contrib import admin

from .models import ActivityLog, FontChoice, Label, ThemePreset, UserPreference


@admin.register(Label)
class LabelAdmin(admin.ModelAdmin):
    list_display = ["key", "group", "en", "ml", "hi", "ta"]
    list_filter = ["group"]
    search_fields = ["key", "default", "en", "ml", "hi", "ta"]


admin.site.register(UserPreference)


@admin.register(FontChoice)
class FontChoiceAdmin(admin.ModelAdmin):
    list_display = ["name", "css_stack", "sort_order", "is_active"]


@admin.register(ThemePreset)
class ThemePresetAdmin(admin.ModelAdmin):
    list_display = ["name", "primary_color", "header_color", "background_color", "sort_order"]


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ["at", "user", "action", "model", "summary"]
    list_filter = ["action", "model"]
    search_fields = ["summary"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
