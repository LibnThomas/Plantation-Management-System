"""
Demo mode: a separate database filled with sample data.

The real data (database "default") is never read or written while demo mode is on; switching
back simply points the session at the real database again.
"""

import importlib

from django.apps import apps
from django.core.management import call_command
from django.db import DatabaseError, connections

from .dbrouter import DEMO_DB, use_db


def _create_postgres_database():
    """Create the demo database next to the real one (needs CREATEDB permission)."""
    name = connections[DEMO_DB].settings_dict["NAME"]
    with connections["default"].cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", [name])
        if not cur.fetchone():
            cur.execute(f'CREATE DATABASE "{name}"')


def prepare():
    """Make sure the demo database exists and has the current tables."""
    if connections[DEMO_DB].vendor == "postgresql":
        _create_postgres_database()
    connections[DEMO_DB].close()
    if _needs_migrate():  # usually up to date: switching demo on stays quick
        call_command("migrate", database=DEMO_DB, interactive=False, verbosity=0)


def _needs_migrate():
    from django.db.migrations.executor import MigrationExecutor

    try:
        executor = MigrationExecutor(connections[DEMO_DB])
        return bool(executor.migration_plan(executor.loader.graph.leaf_nodes()))
    except DatabaseError:
        return True


def has_data():
    from estate.models import Employee

    try:
        with use_db(DEMO_DB):
            return Employee.objects.exists()
    except DatabaseError:
        return False


def _load_initial_lists():
    module = importlib.import_module("estate.migrations.0002_initial_data")
    module.load_initial_data(apps, using=DEMO_DB)


def generate(months=18, reset=False):
    """(Re)build the sample data. reset=True empties the demo database first."""
    from estate.demo_data import generate as run

    prepare()
    if reset:
        call_command("flush", database=DEMO_DB, interactive=False, verbosity=0)
        _load_initial_lists()
    with use_db(DEMO_DB):
        return run(months=months)


def enable():
    """Prepare the demo database (first time: fill it). Returns True if data was generated now."""
    prepare()
    if has_data():
        return False
    generate()
    return True
