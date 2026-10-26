from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("estate", "0018_salary_payment_plot"),
    ]

    operations = [
        migrations.AddField(
            model_name="salarypayment",
            name="blocks",
            field=models.ManyToManyField(
                blank=True,
                help_text="The plots whose work this salary pays for. None: all plots",
                related_name="salary_payments",
                to="estate.block",
                verbose_name="Plots",
            ),
        ),
    ]
