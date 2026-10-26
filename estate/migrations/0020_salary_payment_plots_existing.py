"""Data only (kept apart from the table changes for PostgreSQL): the one plot of payrolls generated for a
plot becomes their plot list."""

from django.db import migrations


def copy(apps, schema_editor):
    using = schema_editor.connection.alias
    SalaryPayment = apps.get_model("estate", "SalaryPayment")
    Through = SalaryPayment.blocks.through
    rows = [
        Through(salarypayment_id=pk, block_id=block_id)
        for pk, block_id in SalaryPayment.objects.using(using)
        .filter(block__isnull=False)
        .values_list("pk", "block_id")
    ]
    Through.objects.using(using).bulk_create(rows, ignore_conflicts=True)


class Migration(migrations.Migration):
    dependencies = [("estate", "0019_salary_payment_plots")]

    operations = [migrations.RunPython(copy, migrations.RunPython.noop)]
