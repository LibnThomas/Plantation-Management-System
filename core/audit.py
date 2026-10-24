"""Activity log: who added, changed or deleted which record (real data only, not demo mode)."""

import contextvars

from django.db.models.signals import post_delete, post_save

from .dbrouter import DEMO_DB

_user = contextvars.ContextVar("pms_audit_user", default=None)


def set_user(user):
    return _user.set(user if user is not None and user.is_authenticated else None)


def reset_user(token):
    _user.reset(token)


def _log(instance, action, using):
    user = _user.get()
    if user is None or using == DEMO_DB:  # scripts, demo data and imports are not logged
        return
    from .models import ActivityLog

    ActivityLog.objects.create(
        user=user,
        action=action,
        model=instance._meta.verbose_name[:60],
        object_id=str(instance.pk)[:40],
        summary=str(instance)[:200],
    )


def _saved(sender, instance, created, using, raw=False, **kwargs):
    if not raw:
        _log(instance, "add" if created else "change", using)


def _deleted(sender, instance, using, **kwargs):
    _log(instance, "delete", using)


def connect(models):
    for model in models:
        uid = f"pms_audit_{model._meta.label_lower}"
        post_save.connect(_saved, sender=model, dispatch_uid=uid + "_save")
        post_delete.connect(_deleted, sender=model, dispatch_uid=uid + "_delete")
