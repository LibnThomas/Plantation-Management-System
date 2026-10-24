"""Data only (kept apart from the table changes: PostgreSQL cannot add indexes in the same transaction
as row updates): each existing harvest gets its product's picking job and its harvest-per-person figures."""

from django.db import migrations
from django.db.models import OuterRef, Subquery


def work_out(apps, schema_editor):
    from estate import productivity

    using = schema_editor.connection.alias
    get = apps.get_model
    Harvest, Product = get("estate", "Harvest"), get("estate", "Product")
    job = Product.objects.using(using).filter(pk=OuterRef("product_id")).values("harvest_job_type_id")[:1]
    Harvest.objects.using(using).filter(job_type__isnull=True).update(job_type_id=Subquery(job))
    models_ = productivity.Models(
        Harvest, get("estate", "GroupWorkEntry"), get("estate", "WorkEntry"), get("estate", "Attendance"),
        get("core", "AppSettings"),
    )
    productivity.refresh_all(using, models_)


class Migration(migrations.Migration):
    dependencies = [
        ("estate", "0009_harvest_ot_and_average"),
        ("core", "0006_work_hours_per_day"),
    ]

    operations = [migrations.RunPython(work_out, migrations.RunPython.noop)]
