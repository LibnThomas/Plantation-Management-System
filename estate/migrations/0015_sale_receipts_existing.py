"""Data only (kept apart from the table changes for PostgreSQL).

- Sale payment statuses get what they mean (Received / Pending / Partially received), so sales can be given
  the right status automatically from the payments received.
- Sales marked Received get one payment received for their full total, on the sold date.
- Sales marked Partially received: the amount is not known, so they stay pending with a note in Comments
  (search 'Partially received') - enter the payments received on them.
"""

from django.db import migrations
from django.db.models import F

NOTE = "[Marked 'Partially received' before payments were tracked - enter the payments received]"


def _means(name):
    name = (name or "").strip().lower()
    if "part" in name:
        return "part"
    if "pending" in name or "not" in name or "due" in name:
        return "pending"
    if "receiv" in name or "paid" in name:
        return "received"
    return ""


def fill(apps, schema_editor):
    using = schema_editor.connection.alias
    Status = apps.get_model("estate", "ReceiptStatus")
    Sale, Receipt = apps.get_model("estate", "Sale"), apps.get_model("estate", "SaleReceipt")
    statuses = {}
    for st in Status.objects.using(using).all():
        st.means = _means(st.name)
        st.save(using=using, update_fields=["means"])
        statuses[st.pk] = st.means
    found = {v for v in statuses.values()}
    order = {"received": 10, "pending": 20, "part": 30}
    for means, name in (("received", "Received"), ("pending", "Pending"), ("part", "Partially received")):
        if means not in found:
            st, _ = Status.objects.using(using).get_or_create(
                name=name, defaults={"sort_order": order[means], "is_active": True}
            )
            st.means = means
            st.save(using=using, update_fields=["means"])
    by_means = {}
    for st in Status.objects.using(using).order_by("-is_active", "sort_order", "id"):
        by_means.setdefault(st.means, st.pk)

    sales = Sale.objects.using(using)
    sales.update(amount_received=0, balance=F("total_amount"), receipt_state="pending")
    received, partly = [], []
    for s in sales.exclude(payment_status__isnull=True).only(
        "id", "date", "total_amount", "payment_status_id", "created_by_id", "remarks"
    ):
        means = statuses.get(s.payment_status_id)
        if means == "received" and s.total_amount > 0:
            received.append(
                Receipt(
                    sale_id=s.pk,
                    date=s.date,
                    amount=s.total_amount,
                    balance_after=0,
                    created_by_id=s.created_by_id,
                    remarks="",
                )
            )
        elif means == "part":
            partly.append(s)
    Receipt.objects.using(using).bulk_create(received, batch_size=500)
    ids = [r.sale_id for r in received]
    sales.filter(pk__in=ids).update(
        amount_received=F("total_amount"),
        balance=0,
        receipt_state="received",
        last_received_on=F("date"),
        payment_status_id=by_means.get("received"),
    )
    for s in partly:
        s.remarks = f"{s.remarks}\n{NOTE}".strip()
        s.save(using=using, update_fields=["remarks"])
    sales.filter(receipt_state="pending").update(payment_status_id=by_means.get("pending"))


class Migration(migrations.Migration):
    dependencies = [("estate", "0014_sale_receipts")]

    operations = [migrations.RunPython(fill, migrations.RunPython.noop)]
