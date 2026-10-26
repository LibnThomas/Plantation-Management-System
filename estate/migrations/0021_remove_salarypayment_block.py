from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("estate", "0020_salary_payment_plots_existing"),
    ]

    operations = [
        migrations.RemoveField(model_name="salarypayment", name="block"),
    ]
