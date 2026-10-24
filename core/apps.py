from django.apps import AppConfig
from django.db.models.signals import post_migrate


def _sync_labels(sender, using="default", **kwargs):
    if using != "default":
        return
    from .i18n import sync_labels

    sync_labels()


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "Core (settings, labels, languages, demo)"

    def ready(self):
        from django.apps import apps

        from . import audit
        from .models import AppSettings

        post_migrate.connect(_sync_labels, sender=self, dispatch_uid="pms_sync_labels")
        audit.connect([*apps.get_app_config("estate").get_models(), AppSettings])
