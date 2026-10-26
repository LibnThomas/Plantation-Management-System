"""Data only (kept apart from the table changes for PostgreSQL).

Existing work entries and Daily Labour Counts: a wage / OT rate that is one of the job type's listed
rates (its default or any wage period) keeps following the wage list; any other amount was typed by hand
and is kept as it is when wage rates change later.
"""

from django.db import migrations


def mark(apps, schema_editor):
    using = schema_editor.connection.alias
    JobType, WageRate = apps.get_model("estate", "JobType"), apps.get_model("estate", "WageRate")
    WorkEntry, Count = apps.get_model("estate", "WorkEntry"), apps.get_model("estate", "GroupWorkEntry")
    for job in JobType.objects.using(using).all():
        rates = WageRate.objects.using(using).filter(job_type_id=job.pk)
        wages = {job.default_daily_wage, *rates.values_list("rate", flat=True)}
        ots = {job.default_ot_rate, *rates.exclude(ot_rate_per_hour=0).values_list("ot_rate_per_hour", flat=True)}
        for model, wage, ot in ((WorkEntry, "wage", "ot_rate"), (Count, "wage_per_worker", "ot_rate_per_hour")):
            rows = model.objects.using(using).filter(job_type_id=job.pk)
            rows.exclude(**{f"{wage}__in": wages}).exclude(**{wage: 0}).update(wage_auto=False)
            rows.exclude(**{f"{ot}__in": ots}).exclude(**{ot: 0}).update(ot_auto=False)


class Migration(migrations.Migration):
    dependencies = [("estate", "0016_wage_rate_scope")]

    operations = [migrations.RunPython(mark, migrations.RunPython.noop)]
