"""
Payroll calculation.

Two kinds of workers are paid:

1. Registered employees - from attendance and individual work details. For every day worked the wage
   and OT rate are taken in this order:
     a. Individual work details - Work Entries for that employee on that day (added up)
     b. Job type                - its wage history for that date (and employee type), else its defaults
   The part of the day that is paid comes from the attendance status (Full day = 1, Half day = 0.5 ...).
   OT hours come from the work entries if they have any, otherwise from attendance.

2. Day labourers in the Daily Labour Count - not registered, entered only as a number per day and
   work type. They are paid per gang leader (who receives the money): man-days x wage per worker
   plus their overtime, exactly as entered on each day's count. Registered employees are never part
   of a count, so nobody is paid twice.
"""

from collections import OrderedDict
from decimal import Decimal

from core.i18n import ui

from . import models as m

SOURCES = OrderedDict(
    [
        ("individual", "Individual work details"),
        ("job", "Job type wage"),
        ("count", "Daily labour count"),
        ("group", "Daily labour count rate"),  # payments made by earlier versions
    ]
)
D0 = Decimal(0)


def source_label(code):
    return ui(SOURCES.get(code, code))


class PayrollContext:
    """Everything payroll needs for a period, loaded with a handful of queries for all employees."""

    def __init__(self, d1, d2, employees=None):
        self.d1, self.d2 = d1, d2
        self.book = m.WageBook(d1, d2)
        att = m.Attendance.objects.filter(date__range=(d1, d2)).select_related("status")
        works = m.WorkEntry.objects.filter(date__range=(d1, d2)).order_by("date", "id")
        if employees is not None:
            att, works = att.filter(employee__in=employees), works.filter(employee__in=employees)
        self.attendance, self.works = {}, {}
        for a in att:
            self.attendance.setdefault(a.employee_id, {})[a.date] = a
        for w in works:
            self.works.setdefault(w.employee_id, {}).setdefault(w.date, []).append(w)


def _day_entries(entries):
    """Several individual entries on one day are added up: (wage, OT hours, OT amount)."""
    wage = sum((w.wage or 0 for w in entries), D0)
    hours = sum((w.ot_hours or 0 for w in entries), D0)
    amount = sum((w.ot_amount or 0 for w in entries), D0)
    return wage, hours, amount


def _result(lines, full, half, leave, ot_total):
    out, basic, ot_amount = [], D0, D0
    for line in lines.values():
        # count lines carry the amounts as saved on each day; employee lines are days x rate
        b = m.q2(line["basic"] if "basic" in line else line["days"] * line["rate"])
        o = m.q2(line["ot_sum"] if "ot_sum" in line else line["ot_hours"] * line["ot_rate"])
        basic += b
        ot_amount += o
        out.append(
            {
                **{k: v for k, v in line.items() if k in ("source", "job", "job_id", "entries")},
                "days": str(line["days"]),
                "rate": str(m.q2(line["rate"])),
                "basic": str(b),
                "ot_hours": str(line["ot_hours"]),
                "ot_rate": str(m.q2(line["ot_rate"])),
                "ot_amount": str(o),
            }
        )
    return {
        "full": full,
        "half": half,
        "leave": leave,
        "ot": ot_total,
        "basic": basic,
        "ot_amount": ot_amount,
        "net": basic + ot_amount,
        "days": sum((Decimal(x["days"]) for x in out), D0),
        "lines": out,
    }


def calculate(emp, d1, d2, ctx=None):
    """A registered employee: None if there is nothing to pay, else day counts, totals and one
    breakdown line per rate used."""
    ctx = ctx or PayrollContext(d1, d2, employees=[emp])
    attendance = ctx.attendance.get(emp.pk, {})
    works = ctx.works.get(emp.pk, {})
    emp_type = emp.employment_type_id

    full = half = leave = 0
    ot_total = D0
    lines = OrderedDict()
    for day in sorted(set(attendance) | set(works)):
        a, entries = attendance.get(day), works.get(day)
        fraction = a.status.pay_fraction if a else Decimal(1)  # a work entry without attendance = full day
        if not fraction:
            leave += bool(a and a.status.is_leave)
            continue
        if fraction >= 1:
            full += 1
        else:
            half += 1
        job_id = (entries[0].job_type_id if entries else None) or (a.job_type_id if a else None) or emp.job_type_id
        standard = ctx.book.wages_on(job_id, day, emp_type)
        if entries and any(w.wage for w in entries):
            rate, ot_hours, ot_amount = _day_entries(entries)
            ot_rate = m.q2(ot_amount / ot_hours) if ot_hours else entries[0].ot_rate
            source = "individual"
        else:
            ot_hours = (sum((w.ot_hours or 0 for w in entries), D0) if entries else 0) or (a.ot_hours if a else 0) or D0
            rate, ot_rate = standard
            source = "job"
        ot_rate = ot_rate or standard[1]

        line = lines.setdefault(
            (source, rate, ot_rate),
            {"source": source, "rate": rate, "ot_rate": ot_rate, "days": D0, "ot_hours": D0},
        )
        line["days"] += fraction
        line["ot_hours"] += ot_hours
        ot_total += ot_hours

    return _result(lines, full, half, leave, ot_total) if lines else None


def calculate_counts(d1, d2, job_type=None):
    """Daily Labour Count entries of the period, per gang leader (None = entries without one):
    {gang_leader_id: result}. One breakdown line per work type and rate."""
    qs = m.GroupWorkEntry.objects.filter(date__range=(d1, d2)).select_related("job_type").order_by("date", "id")
    if job_type is not None:
        qs = qs.filter(job_type=job_type)
    per_leader = OrderedDict()
    for g in qs:
        if not g.workers_count and not g.ot_total_hours:
            continue
        lines = per_leader.setdefault(g.gang_leader_id, OrderedDict())
        line = lines.setdefault(
            (g.job_type_id, g.wage_per_worker, g.ot_rate_per_hour),
            {
                "source": "count",
                "job": g.job_type.name,
                "job_id": g.job_type_id,
                "entries": 0,
                "rate": g.wage_per_worker or D0,
                "ot_rate": g.ot_rate_per_hour or D0,
                "days": D0,
                "ot_hours": D0,
                "basic": D0,
                "ot_sum": D0,
            },
        )
        line["entries"] += 1
        line["days"] += g.workers_count  # man-days
        line["ot_hours"] += g.ot_total_hours or D0
        line["basic"] += g.total_wages or D0  # as saved on the day (count x wage)
        line["ot_sum"] += g.ot_amount or D0
    results = OrderedDict()
    for leader_id, lines in per_leader.items():
        man_days = sum((x["days"] for x in lines.values()), D0)
        ot_total = sum((x["ot_hours"] for x in lines.values()), D0)
        results[leader_id] = _result(lines, man_days, 0, 0, ot_total)
    return results


def summary(lines):
    """Short English text for the remarks column, e.g. '212 x 650 Cardamom picking (Daily labour count)'."""
    parts = []
    for x in lines:
        job = f" {x['job']}" if x.get("job") else ""
        parts.append(
            f"{Decimal(x['days']).normalize():f} x {Decimal(x['rate']).normalize():f}{job} "
            f"({SOURCES.get(x['source'], x['source'])})"
        )
    return "; ".join(parts)


def default_status():
    """The first active status that is not final (e.g. "Pending")."""
    return m.PaymentStatus.objects.filter(is_active=True, is_final=False).order_by("sort_order", "id").first()


def existing_count_payments(d1, d2, gang_leader_id):
    """Daily Labour Count payments of a gang leader (None = without one) overlapping a period."""
    return m.SalaryPayment.objects.filter(
        employee__isnull=True, gang_leader_id=gang_leader_id, period_from__lte=d2, period_to__gte=d1
    )


def save(emp, d1, d2, calc, user=None, status=None, payment=None, gang_leader=None, **extra):
    """Create a salary payment (for an employee, or for a gang leader's Daily Labour Count when emp is
    None), or re-calculate `payment` keeping what was typed by hand (bonus, deductions, payment date /
    mode, status)."""
    p = payment or m.SalaryPayment(
        employee=emp, gang_leader=None if emp else gang_leader, created_by=user, status=status or default_status()
    )
    p.period_from, p.period_to = d1, d2
    p.full_days, p.half_days, p.leave_days = calc["full"], calc["half"], calc["leave"]
    p.ot_hours, p.breakdown = calc["ot"], calc["lines"]
    p.remarks = summary(calc["lines"])[:200]
    for key, value in extra.items():
        setattr(p, key, value)
    p.save()
    return p
