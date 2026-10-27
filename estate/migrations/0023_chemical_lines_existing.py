"""Existing fertilizer & field-work entries (one chemical each) become one chemical line each.

Each chemical already used gets one 'Opening stock' purchase at its old cost per unit, sized to what was
used, so old entries keep exactly their cost and the stock starts at 0 until real purchases are entered."""

from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from django.db import migrations

D0 = Decimal(0)


def q2(v):
    return Decimal(v or 0).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _n(v):
    text = f"{v or 0:f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def forwards(apps, schema_editor):
    using = schema_editor.connection.alias
    AgriInput = apps.get_model("estate", "AgriInput")
    FieldOperation = apps.get_model("estate", "FieldOperation")
    Purchase = apps.get_model("estate", "AgriInputPurchase")
    Line = apps.get_model("estate", "FieldOperationInput")
    ops_by_input = defaultdict(list)
    for op in FieldOperation.objects.using(using).filter(agri_input__isnull=False).order_by("date", "id"):
        ops_by_input[op.agri_input_id].append(op)
    for a in AgriInput.objects.using(using).select_related("unit"):
        ops = ops_by_input.get(a.pk, [])
        if not ops:  # never used: its cost per unit stays as the price until a purchase is entered
            continue
        used = sum((Decimal(o.quantity or 0) for o in ops), D0)
        price = Decimal(a.unit_cost or 0)
        purchase = Purchase.objects.using(using).create(
            agri_input=a,
            date=ops[0].date,
            bags=used,
            pack_size=Decimal(1),
            price_per_bag=price,
            supplier="Opening stock",
            remarks="Created automatically from the cost per unit before purchases were recorded.",
            quantity=used,
            total_amount=q2(used * price),
            unit_cost=price,
            used_qty=used,
            left_qty=D0,
        )
        unit = a.unit.name if a.unit_id else ""
        for op in ops:
            qty, cost_unit = Decimal(op.quantity or 0), Decimal(op.unit_cost or 0)
            Line.objects.using(using).create(
                operation=op,
                agri_input=a,
                purchase=purchase,
                quantity=qty,
                unit_cost=cost_unit,
                cost=q2(qty * cost_unit),
            )
            FieldOperation.objects.using(using).filter(pk=op.pk).update(
                chemicals_text=f"{a.name} {_n(qty)} {unit}".strip()[:400]
            )
        AgriInput.objects.using(using).filter(pk=a.pk).update(purchased_qty=used, used_qty=used, stock_qty=D0)


class Migration(migrations.Migration):
    dependencies = [("estate", "0022_chemical_purchases_and_lines")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
