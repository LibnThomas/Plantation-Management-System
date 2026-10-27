"""'Dosage notes' on field work is removed: any text in it is kept at the end of the entry's comments."""

from django.db import migrations


def forwards(apps, schema_editor):
    using = schema_editor.connection.alias
    FieldOperation = apps.get_model("estate", "FieldOperation")
    for op in FieldOperation.objects.using(using).exclude(dosage=""):
        note = f"Dosage: {op.dosage}"
        op.remarks = f"{op.remarks}\n{note}".strip() if op.remarks else note
        op.save(update_fields=["remarks"])


class Migration(migrations.Migration):
    dependencies = [("estate", "0025_mix_ratio")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
