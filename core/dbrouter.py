"""
Database routing for demo mode.

Business data (the `estate` app) lives in the alias chosen for the current request:
"default" for the real estate data, "demo" for the sample data. Users, sessions,
labels / translations and user preferences are shared and always use "default".
"""

import contextvars
from contextlib import contextmanager

DEFAULT_DB = "default"
DEMO_DB = "demo"
SHARED_APPS = {"admin", "auth", "contenttypes", "sessions", "core"}

_active_db = contextvars.ContextVar("pms_active_db", default=DEFAULT_DB)


def active_db():
    return _active_db.get()


def set_active_db(alias):
    """Returns a token for reset_active_db()."""
    return _active_db.set(alias)


def reset_active_db(token):
    _active_db.reset(token)


@contextmanager
def use_db(alias):
    token = _active_db.set(alias)
    try:
        yield alias
    finally:
        _active_db.reset(token)


class PMSRouter:
    def _route(self, model):
        return DEFAULT_DB if model._meta.app_label in SHARED_APPS else _active_db.get()

    def db_for_read(self, model, **hints):
        return self._route(model)

    def db_for_write(self, model, **hints):
        return self._route(model)

    def allow_relation(self, obj1, obj2, **hints):
        # estate rows keep a reference (no DB constraint) to the shared auth user
        return True

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if db == DEMO_DB:
            return app_label not in SHARED_APPS
        return True
