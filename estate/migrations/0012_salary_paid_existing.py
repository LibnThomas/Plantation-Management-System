"""Data only (kept apart from the table changes for PostgreSQL): payrolls already marked with a final status
(e.g. Paid) get one payment entry for their full net amount; the others are pending."""

from django.db import migrations
from django.db.models import F


def fill(apps, schema_editor):
    using = schema_editor.connection.alias
    Salary, Paid = apps.get_model("estate", "SalaryPayment"), apps.get_model("estate", "SalaryPaid")
    salaries = Salary.objects.using(using)
    salaries.update(balance=F("net_amount"), amount_paid=0, pay_state="unpaid")
    paid = []
    for s in salaries.filter(status__is_final=True, net_amount__gt=0):
        paid.append(
            Paid(
                salary_id=s.pk,
                date=s.payment_date or s.period_to,
                amount=s.net_amount,
                payment_mode_id=s.payment_mode_id,
                balance_after=0,
                created_by_id=s.created_by_id,
                remarks="",
            )
        )
    Paid.objects.using(using).bulk_create(paid, batch_size=500)
    salaries.filter(status__is_final=True).update(amount_paid=F("net_amount"), balance=0, pay_state="paid")


class Migration(migrations.Migration):
    dependencies = [("estate", "0011_salary_part_payments")]

    operations = [migrations.RunPython(fill, migrations.RunPython.noop)]
