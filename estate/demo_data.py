"""
Sample data generator for Demo mode.

Writes ONLY to the demo database (see core.demo). It builds everything from what is in the
drop-down lists and masters (job types, crops, fertilizers ...), so it keeps working after
those lists are renamed or extended.
"""

import datetime as dt
import random
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Sum

from core.dbrouter import DEMO_DB, active_db

from . import models as m
from . import payroll, productivity

FIRST_NAMES = [
    "Rajan",
    "Suresh",
    "Murugan",
    "Selvi",
    "Lakshmi",
    "Anitha",
    "Kumar",
    "Bindu",
    "Ramesh",
    "Shiny",
    "Ponnamma",
    "Thomas",
    "Mary",
    "Ganesan",
    "Valli",
    "Muthu",
    "Saritha",
    "Biju",
    "Mini",
    "Arjun",
    "Pandi",
    "Rani",
    "Joseph",
    "Kavitha",
    "Mani",
    "Sunitha",
    "Babu",
    "Jaya",
]
INITIALS = "KPMSRTVJ"


def _r(lo, hi, places=1):
    return D(str(round(random.uniform(lo, hi), places)))


def _month_starts(start, end):
    d = start.replace(day=1)
    while d <= end:
        yield d
        d = (d + dt.timedelta(days=32)).replace(day=1)


def _pick(qs):
    items = list(qs)
    return random.choice(items) if items else None


class Generator:
    def __init__(self, months=18, today=None, seed=42):
        if active_db() != DEMO_DB:
            raise RuntimeError("Demo data can only be generated in the demo database.")
        random.seed(seed)
        self.today = today or dt.date.today()
        self.start = (self.today.replace(day=1) - dt.timedelta(days=30 * months)).replace(day=1)
        admin = get_user_model().objects.filter(is_superuser=True).order_by("id").first()
        self.user_id = admin.pk if admin else None

    # ---------------------------------------------------------------- lists
    def _lists(self):
        statuses = list(m.AttendanceStatus.objects.filter(is_active=True))
        self.full = next((s for s in statuses if s.pay_fraction >= 1), statuses[0])
        self.half = next((s for s in statuses if 0 < s.pay_fraction < 1), self.full)
        self.leave = next((s for s in statuses if s.is_leave), self.full)
        self.absent = next((s for s in statuses if not s.pay_fraction and not s.is_leave), self.leave)
        self.jobs = list(m.JobType.objects.filter(is_active=True))
        self.products = [p for p in m.Product.objects.filter(is_active=True) if p.harvest_month_list]
        if not self.jobs or not self.products:
            raise RuntimeError("Add job types and products (with harvest months) before generating demo data.")
        self.main = self.products[0]
        harvest_job_ids = {p.harvest_job_type_id for p in self.products}
        self.other_jobs = [j for j in self.jobs if j.pk not in harvest_job_ids] or self.jobs
        self.paid_status = m.PaymentStatus.objects.filter(is_final=True).first() or m.PaymentStatus.first_active()
        self.pay_mode = m.PaymentMode.first_active()

    # ---------------------------------------------------------------- masters
    def _masters(self):
        self.leaders = [
            m.GangLeader.objects.create(name=name, phone=f"94470{random.randint(10000, 99999)}", address=place)
            for name, place in (
                ("Murugan (kangani)", "Bodinayakanur"),
                ("Selvi (kangani)", "Cumbum"),
                ("Raju contractor", "Kumily"),
            )
        ]
        crops = [self.main, self.main, self.main, self.products[-1]]
        self.blocks = []
        for i, (area, crop) in enumerate(zip((6.5, 5.0, 4.0, 3.0), crops, strict=True)):
            self.blocks.append(
                m.Block.objects.create(
                    name=f"Block {'ABCD'[i]}",
                    area_acres=D(str(area)),
                    main_crop=crop,
                    plant_count=int(area * 650),
                    year_planted=self.today.year - random.randint(5, 14),
                    irrigated=i == 0,
                    created_by_id=self.user_id,
                )
            )
        y = self.today.year
        for j in self.jobs:
            base, ot = j.default_daily_wage, j.default_ot_rate
            periods = [
                (self.start, dt.date(y - 1, 12, 31), base * D("0.9"), ot * D("0.9")),
                (dt.date(y, 1, 1), dt.date(y, 6, 30), base, ot),
                (dt.date(y, 7, 1), None, base * D("1.1"), ot * D("1.1")),
            ]
            for f, t, rate, ot_rate in periods:
                if (t and t < f) or f > self.today:
                    continue
                m.WageRate.objects.create(
                    job_type=j,
                    effective_from=f,
                    effective_to=t,
                    rate=rate.quantize(D(1)),
                    ot_rate_per_hour=ot_rate.quantize(D(1)),
                    created_by_id=self.user_id,
                )
        emp_types = list(m.EmploymentType.objects.filter(is_active=True)[:2])
        genders = list(m.Gender.objects.filter(is_active=True)[:2])
        job_plan = [self.main.harvest_job_type or self.jobs[0]] * 9 + [
            self.other_jobs[i % len(self.other_jobs)] for i in range(15)
        ]
        self.employees = []
        for i, job in enumerate(job_plan):
            self.employees.append(
                m.Employee.objects.create(
                    full_name=f"{FIRST_NAMES[i % len(FIRST_NAMES)]} {random.choice(INITIALS)}",
                    gender=genders[i % len(genders)] if genders else None,
                    phone=f"94470{random.randint(10000, 99999)}",
                    job_type=job,
                    employment_type=emp_types[1 if i % 3 == 0 and len(emp_types) > 1 else 0],
                    address="Idukki, Kerala",
                    date_of_joining=self.start - dt.timedelta(days=random.randint(30, 2000)),
                    created_by_id=self.user_id,
                )
            )

    # ---------------------------------------------------------------- daily records
    def _attendance(self):
        rows, d = [], self.start
        while d <= self.today:
            if d.weekday() != 6:
                for e in self.employees:
                    r = random.random()
                    status = (
                        self.full if r < 0.82 else self.half if r < 0.9 else self.leave if r < 0.95 else self.absent
                    )
                    ot = D(random.choice([0, 0, 0, 0, 1, 2])) if status is self.full else D(0)
                    rows.append(
                        m.Attendance(
                            employee=e,
                            date=d,
                            status=status,
                            ot_hours=ot,
                            block=random.choice(self.blocks),
                            created_by_id=self.user_id,
                        )
                    )
            d += dt.timedelta(days=1)
        m.Attendance.objects.bulk_create(rows, batch_size=2000)

    def _operations(self):
        process = m.ProcessType.first_active()
        workers_extra = self.employees[-3:]
        d = self.start
        while d <= self.today:
            wd = d.weekday()
            if wd == 6:
                d += dt.timedelta(days=1)
                continue
            for k, p in enumerate(self.products):
                if d.month not in p.harvest_month_list or wd not in ((0, 2, 4), (1,), (3,))[min(k, 2)]:
                    continue
                block = random.choice([b for b in self.blocks if b.main_crop_id == p.pk] or self.blocks)
                gang = random.randint(6, 16)
                peak = 1.5 if d.month == p.harvest_month_list[len(p.harvest_month_list) // 3] else 1.0
                qty = _r(gang * 30 * peak, gang * 60 * peak)
                ot_pickers = random.choice([0, 0, 0, 2, 3, 4])  # some evenings a few stay on to pick
                ot_qty = _r(ot_pickers * 4, ot_pickers * 9) if ot_pickers else D(0)
                m.Harvest.objects.create(
                    date=d,
                    product=p,
                    block=block,
                    quantity=qty + ot_qty,
                    created_by_id=self.user_id,
                    harvest_round=f"Round {(d - self.start).days // 20 % 8 + 1}",
                )
                if p.harvest_job_type_id:
                    m.GroupWorkEntry.objects.create(
                        date=d,
                        job_type=p.harvest_job_type,
                        block=block,
                        workers_count=gang,
                        ot_evening_workers=ot_pickers,
                        ot_evening_hours=D(2) if ot_pickers else D(0),
                        gang_leader=self.leaders[k % 2],
                        created_by_id=self.user_id,
                    )
                    for e in random.sample(self.employees, min(2, len(self.employees))):
                        # a couple of registered employees pick with the gang (Work Details - Individual)
                        m.WorkEntry.objects.create(
                            date=d,
                            employee=e,
                            job_type=p.harvest_job_type,
                            block=block,
                            ot_hours=D(random.choice([0, 0, 0, 2])),
                            created_by_id=self.user_id,
                        )
                recovery = (p.typical_recovery_percent or D(25)) / 100 * _r(0.93, 1.07, 3)
                if process:
                    m.ProcessingBatch.objects.create(
                        date=d + dt.timedelta(days=1 + k * 3),
                        product=p,
                        process_type=process,
                        input_quantity=qty + ot_qty,
                        output_quantity=(qty * recovery).quantize(D("0.1")),
                        processing_cost=_r(1.5, 7).quantize(D("0.01")),  # per kg of input
                        created_by_id=self.user_id,
                    )
            if wd in (1, 3, 5) and self.other_jobs:
                job = random.choice(self.other_jobs)
                w = random.randint(4, 10)
                special = job.wages_on(d)[0] + 50 if random.random() < 0.1 else 0
                m.GroupWorkEntry.objects.create(
                    date=d,
                    gang_leader=self.leaders[2] if random.random() < 0.7 else random.choice(self.leaders),
                    job_type=job,
                    block=random.choice(self.blocks),
                    workers_count=w,
                    male_workers=w // 2,
                    female_workers=w - w // 2,
                    wage_per_worker=special,
                    ot_morning_workers=random.choice([0, 0, 2]),
                    ot_morning_hours=D(random.choice([1, 1.5])),
                    ot_evening_workers=random.choice([0, 1, 3]),
                    ot_evening_hours=D(random.choice([1, 2])),
                    area_covered_acres=D(random.choice([1, 2])),
                    remarks="Steep block - extra wage" if special else "",
                    created_by_id=self.user_id,
                )
            if wd in (0, 3):
                for e in workers_extra:
                    m.WorkEntry.objects.create(
                        date=d,
                        employee=e,
                        job_type=e.job_type,
                        block=random.choice(self.blocks),
                        ot_hours=D(random.choice([0, 0, 1, 2])),
                        created_by_id=self.user_id,
                    )
            d += dt.timedelta(days=1)

    def _field_work(self):
        ops = list(m.OperationType.objects.filter(is_active=True).select_related("input_type"))
        with_input = [
            o for o in ops if o.input_type_id and m.AgriInput.objects.filter(input_type=o.input_type_id).exists()
        ]
        manual = [o for o in ops if not o.input_type_id]
        methods = list(m.ApplicationMethod.objects.filter(is_active=True))
        for month in _month_starts(self.start, self.today):
            for block in self.blocks:
                plan = random.sample(with_input, k=min(2, len(with_input)))
                if manual and month.month % 3 == 0:
                    plan.append(random.choice(manual))
                for op in plan:
                    day = month + dt.timedelta(days=random.randint(2, 25))
                    if day > self.today:
                        continue
                    agri = _pick(m.AgriInput.objects.filter(input_type=op.input_type_id)) if op.input_type_id else None
                    m.FieldOperation.objects.create(
                        date=day,
                        operation_type=op,
                        block=block,
                        agri_input=agri,
                        quantity=(block.area_acres * _r(5, 40)).quantize(D("0.1")) if agri else 0,
                        method=random.choice(methods) if methods else None,
                        workers_count=random.randint(2, 6),
                        area_covered_acres=block.area_acres,
                        purpose=op.name,
                        next_due_date=day + dt.timedelta(days=random.choice([45, 60, 90])) if agri else None,
                        created_by_id=self.user_id,
                    )

    def _finance(self):
        categories = list(m.ExpenseCategory.objects.filter(is_active=True))
        receipt = m.ReceiptStatus.first_active()
        for month in _month_starts(self.start, self.today):
            for cat in random.sample(categories, k=min(4, len(categories))):
                day = month + dt.timedelta(days=random.randint(1, 25))
                if day <= self.today:
                    m.Expense.objects.create(
                        date=day,
                        category=cat,
                        description=cat.name,
                        vendor="Local",
                        amount=D(random.randint(10, 100) * 100),
                        created_by_id=self.user_id,
                    )
            sale_day = month + dt.timedelta(days=random.randint(20, 27))
            if sale_day > self.today:
                continue
            for p in self.products:
                produced = m.ProcessingBatch.objects.filter(
                    product=p, date__year=month.year, date__month=month.month
                ).aggregate(s=Sum("output_quantity"))["s"]
                if produced:
                    sale = m.Sale.objects.create(
                        date=sale_day,
                        product=p,
                        quantity=produced,
                        rate=(p.typical_rate * _r(0.85, 1.2, 2)).quantize(D(1)),
                        buyer="e-auction" if p == self.main else "Local trader",
                        payment_status=receipt,
                        created_by_id=self.user_id,
                    )
                    # auction: paid in full a few days later; a trader pays in parts (30 %, 20 %, the rest)
                    plan = [(3, 1)] if p == self.main else [(0, D("0.3")), (7, D("0.2")), (8, 1)]
                    self._receive(sale, plan)

    def _receive(self, sale, plan):
        """Payments received for a demo sale; the ones after today are not received yet."""
        left = sale.total_amount
        for days, share in plan:
            day = sale.date + dt.timedelta(days=days)
            amount = left if share == 1 else (sale.total_amount * share).quantize(D(1))
            if day > self.today or amount <= 0:
                break
            m.SaleReceipt.objects.create(
                sale=sale, date=day, amount=amount, payment_mode=self.pay_mode, created_by_id=self.user_id
            )
            left -= amount

    def _pay(self, p, day, parts=(1,)):
        """Payments made against a demo payroll: in full, or in parts (the rest stays pending)."""
        left = p.net_amount
        for i, share in enumerate(parts):
            amount = left if share == 1 else (p.net_amount * D(str(share))).quantize(D(1))
            if amount <= 0:
                break
            m.SalaryPaid.objects.create(
                salary=p,
                date=day + dt.timedelta(days=i * 2),
                amount=amount,
                payment_mode=self.pay_mode,
                created_by_id=self.user_id,
            )
            left -= amount

    def _payroll(self):
        end = self.today.replace(day=1) - dt.timedelta(days=1)
        for month in range(3):
            start = end.replace(day=1)
            ctx = payroll.PayrollContext(start, end)
            for e in self.employees:
                calc = payroll.calculate(e, start, end, ctx)
                if calc:
                    p = payroll.save(e, start, end, calc, status=self.paid_status)
                    self._pay(p, end + dt.timedelta(days=3))
            for k, (leader_id, calc) in enumerate(payroll.calculate_counts(start, end).items()):
                p = payroll.save(
                    None,
                    start,
                    end,
                    calc,
                    gang_leader=next((g for g in self.leaders if g.pk == leader_id), None),
                    status=self.paid_status,
                )
                # last month: one gang leader paid in instalments, another only partly (balance pending)
                parts = {0: (0.7, 0.15), 1: (0.6,)}.get(k, (1,)) if month == 0 else (1,)
                self._pay(p, end + dt.timedelta(days=2), parts)
            end = start - dt.timedelta(days=1)

    def run(self):
        with transaction.atomic(using=DEMO_DB), productivity.paused():
            self._lists()
            self._masters()
            self._attendance()
            self._operations()
            self._field_work()
            self._finance()
            self._payroll()
        return {"employees": len(self.employees), "from": self.start, "to": self.today}


def generate(months=18, today=None):
    return Generator(months=months, today=today).run()
