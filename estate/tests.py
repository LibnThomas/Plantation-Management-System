import datetime as dt
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, TransactionTestCase

from core.dbrouter import DEFAULT_DB, DEMO_DB, use_db
from core.models import Label

from . import models as m
from . import payroll
from .registry import LOOKUP_MODULES


def lookup(model, **kw):
    """Initial list values come from migration 0002 - nothing is hard-coded in the app."""
    return model.objects.filter(**kw).order_by("sort_order", "id").first()


class Base(TestCase):
    def setUp(self):
        self.permanent = lookup(m.EmploymentType)
        self.other_type = m.EmploymentType.objects.exclude(pk=self.permanent.pk).first()
        self.full = lookup(m.AttendanceStatus, pay_fraction=1)
        self.half = lookup(m.AttendanceStatus, pay_fraction=D("0.5"))
        self.job = m.JobType.objects.create(name="Test picking", default_daily_wage=D(500), default_ot_rate=D(70))
        self.emp = m.Employee.objects.create(full_name="Test", job_type=self.job, employment_type=self.permanent)
        self.prod = m.Product.objects.create(name="Test crop")

    def attend(self, day, status=None, ot=0, emp=None):
        return m.Attendance.objects.create(
            employee=emp or self.emp, date=day, status=status or self.full, ot_hours=D(ot)
        )


class InitialDataTests(TestCase):
    def test_lists_are_seeded_with_translations(self):
        for model in m.LOOKUP_MODELS:
            self.assertTrue(model.objects.exists(), model)
        status = lookup(m.AttendanceStatus, pay_fraction=1)
        self.assertTrue(status.name_ml and status.name_hi and status.name_ta)


class CalculationTests(Base):
    def test_employee_code_and_rates_from_job(self):
        self.assertEqual(self.emp.employee_code, "EMP0001")
        self.assertEqual(self.emp.wages_on(dt.date(2026, 1, 1)), (D(500), D(70)))

    def test_work_entry_totals(self):
        w = m.WorkEntry.objects.create(employee=self.emp, job_type=self.job, ot_hours=D(2))
        self.assertEqual((w.wage, w.ot_amount, w.total_amount), (D(500), D(140), D(640)))

    def test_group_work_totals(self):
        g = m.GroupWorkEntry.objects.create(
            job_type=self.job,
            male_workers=4,
            female_workers=6,
            ot_morning_workers=2,
            ot_evening_workers=3,
            ot_morning_hours=D(1),
            ot_evening_hours=D(2),
        )
        self.assertEqual(g.workers_count, 10)
        self.assertEqual((g.total_wages, g.wage_per_worker), (D(5000), D(500)))
        self.assertEqual((g.ot_total_hours, g.ot_amount, g.total_cost), (D(8), D(560), D(5560)))
        g.female_workers = 8  # changing the split later keeps the total right
        g.save()
        self.assertEqual((g.workers_count, g.total_wages), (12, D(6000)))

    def test_labour_count_with_half_days(self):
        # 14 came, one went home at noon: 13.5; one of the 3 evening OT workers stayed half the time
        g = m.GroupWorkEntry.objects.create(
            job_type=self.job,
            male_workers=D("6.5"),
            female_workers=7,
            wage_per_worker=D(600),
            ot_rate_per_hour=D(80),
            ot_evening_workers=D("2.5"),
            ot_evening_hours=2,
        )
        g.refresh_from_db()
        self.assertEqual((g.workers_count, g.total_wages), (D("13.5"), D(8100)))
        self.assertEqual((g.ot_total_hours, g.ot_amount, g.total_cost), (D(5), D(400), D(8500)))
        res = payroll.calculate_counts(g.date, g.date)[None]
        self.assertEqual((res["full"], res["net"]), (D("13.5"), D(8500)))

    def test_processing_ratio(self):
        b = m.ProcessingBatch.objects.create(
            product=self.prod, process_type=lookup(m.ProcessType), input_quantity=D(500), output_quantity=D(100)
        )
        self.assertEqual((b.conversion_ratio, b.recovery_percent), (D("5.000"), D("20.00")))
        self.assertTrue(b.batch_no.startswith("PB"))

    def test_harvest_per_person_in_hours(self):
        from core.models import AppSettings

        picking = self.job
        self.prod.harvest_job_type = picking
        self.prod.save()
        day = dt.date(2026, 10, 5)
        # individual: 2 employees, one of them 2 OT hours; labour count: 8 workers, 2 of them 2 OT hours each
        other = m.Employee.objects.create(full_name="Second", job_type=picking, employment_type=self.permanent)
        self.attend(day)  # present; the other employee has no attendance marked: counts as a full day
        m.WorkEntry.objects.create(employee=self.emp, job_type=picking, date=day, ot_hours=2)
        m.WorkEntry.objects.create(employee=other, job_type=picking, date=day)
        count = m.GroupWorkEntry.objects.create(
            job_type=picking, date=day, workers_count=8, ot_evening_workers=2, ot_evening_hours=2
        )
        h = m.Harvest.objects.create(product=self.prod, date=day, quantity=200)
        h.refresh_from_db()
        self.assertEqual(h.job_type, picking)  # from the product
        self.assertEqual((h.ind_people, h.ind_ot_people, h.ind_ot_hours), (D(2), D(1), D(2)))
        self.assertEqual((h.count_people, h.count_ot_people, h.count_ot_hours), (D(8), D(2), D(4)))
        self.assertEqual(h.total_hours, D(86))  # 10 people x 8 h + 6 OT hours
        self.assertEqual(h.per_hour, D("2.326"))  # 200 / 86
        self.assertEqual(h.per_person_full_day, D("18.605"))  # x 8 h
        self.assertEqual(h.per_person_full_day_ot, D("23.256"))  # x (8 + 2 average OT hours)
        self.assertEqual(h.per_person_ot, D("4.651"))  # x 2 average OT hours
        self.assertEqual(h.per_person_average, D(20))  # 200 / 10 people
        self.assertEqual((h.full_day_quantity, h.ot_quantity), (D("186.047"), D("13.953")))  # = 200 kg
        # attendance changes later: half day counts 4 hours, absent is not counted
        att = m.Attendance.objects.create(employee=other, date=day, status=self.half)
        h.refresh_from_db()
        self.assertEqual((h.ind_people, h.total_hours), (D("1.5"), D(82)))
        att.status = lookup(m.AttendanceStatus, pay_fraction=0)
        att.save()
        h.refresh_from_db()
        self.assertEqual((h.ind_people, h.total_hours), (D(1), D(78)))
        # the labour count changes / goes
        count.delete()
        h.refresh_from_db()
        self.assertEqual((h.count_people, h.total_hours, h.per_hour), (D(0), D(10), D(20)))
        # a second harvest of the same day and job: quantities are added together
        m.Harvest.objects.create(product=self.prod, date=day, quantity=100)
        h.refresh_from_db()
        self.assertEqual(h.per_hour, D(30))
        # hours in a full day come from App Settings
        AppSettings.objects.filter(pk=1).update(work_hours_per_day=10)
        from . import productivity

        productivity.refresh_all()
        h.refresh_from_db()
        self.assertEqual((h.total_hours, h.per_person_full_day), (D(12), D(250)))

    def test_processing_cost_is_per_kg(self):
        b = m.ProcessingBatch.objects.create(
            product=self.prod,
            process_type=lookup(m.ProcessType),
            input_quantity=D(100),
            output_quantity=D(22),
            processing_cost=D(11),
        )
        self.assertEqual(b.total_cost, D(1100))  # 100 kg x 11 per kg

    def test_dates_are_dd_mm_yy(self):
        from core import dates

        self.assertEqual(dates.fmt(dt.date(2026, 10, 13)), "13/10/26")
        for text in ("13/10/26", "13/10/2026", "2026-10-13", "13-10-26", "13.10.2026"):
            self.assertEqual(dates.parse(text), dt.date(2026, 10, 13), text)
        self.assertEqual(dates.parse("15/06/65"), dt.date(1965, 6, 15))  # a birth date, not 2065
        self.assertIsNone(dates.parse("31/02/26"))

    def test_chemical_purchases_keep_their_price(self):
        kg = lookup(m.Unit)
        npk = m.AgriInput.objects.create(
            name="Test NPK", unit=kg, pack_size=D(25), dose_qty=D("2.5"), dose_basis="water", dose_water=D(500)
        )
        jan = m.AgriInputPurchase.objects.create(
            agri_input=npk, date=dt.date(2026, 1, 5), bags=D(10), price_per_bag=D(250), supplier="A"
        )
        m.AgriInputPurchase.objects.create(agri_input=npk, date=dt.date(2026, 3, 5), bags=D(10), price_per_bag=D(450))
        self.assertEqual(
            (jan.pack_size, jan.quantity, jan.total_amount, jan.unit_cost), (D(25), D(250), D(2500), D(10))
        )
        npk.refresh_from_db()
        self.assertEqual((npk.purchased_qty, npk.stock_qty, npk.unit_cost), (D(500), D(500), D(18)))  # latest price
        # 2.5 kg per 500 L jar, mixed 4 times -> 10 kg
        op = m.FieldOperation.objects.create(
            date=dt.date(2026, 2, 1),
            operation_type=lookup(m.OperationType),
            tank_litres=D(500),
            rounds=D(4),
        )
        line = m.FieldOperationInput.objects.create(
            operation=op, agri_input=npk, purchase=jan, dose_qty=D("2.5"), dose_basis="water", dose_water=D(500)
        )
        self.assertEqual((line.quantity, line.unit_cost, line.cost), (D(10), D(10), D(100)))
        op.refresh_from_db()
        self.assertEqual((op.input_cost, op.total_cost), (D(100), D(100)))
        # the same ratio in a 1000 L jar: 5 kg per mix, x 4 mixes = 20 kg
        op.tank_litres = D(1000)
        self.assertEqual(op.chemical_qty(D("2.5"), "water", D(500)), D(20))
        self.assertEqual(op.chemical_qty(D(5), "water", D(1000)), D(20))
        op.tank_litres = D(500)
        self.assertIn("Test NPK 10", op.chemicals_text)
        jan.refresh_from_db()
        npk.refresh_from_db()
        self.assertEqual((jan.used_qty, jan.left_qty, npk.used_qty, npk.stock_qty), (D(10), D(240), D(10), D(490)))
        # a cheaper purchase later never changes the old entry
        m.AgriInputPurchase.objects.create(agri_input=npk, date=dt.date(2026, 6, 5), bags=D(4), price_per_bag=D(100))
        line.refresh_from_db()
        op.refresh_from_db()
        npk.refresh_from_db()
        self.assertEqual((line.unit_cost, op.input_cost, npk.unit_cost), (D(10), D(100), D(4)))
        # dry mix: 50 kg of cake in one batch, 4 batches made -> 200 kg
        cake = m.AgriInput.objects.create(name="Test cake", unit=kg, pack_size=D(50))
        line2 = m.FieldOperationInput.objects.create(
            operation=op, agri_input=cake, dose_qty=D(50), dose_basis="batch", unit_cost=D(2)
        )
        self.assertEqual((line2.quantity, line2.cost), (D(200), D(400)))
        cake.refresh_from_db()
        self.assertEqual(cake.stock_qty, D(-200))  # used without a purchase: shown as short, still saved
        # removing a line / the whole entry gives the stock back
        line2.delete()
        op.refresh_from_db()
        self.assertEqual(op.input_cost, D(100))
        op.delete()
        npk.refresh_from_db()
        jan.refresh_from_db()
        self.assertEqual((npk.stock_qty, jan.left_qty), (D(600), D(250)))

    def test_manual_salary(self):
        p = m.SalaryPayment.objects.create(
            employee=self.emp,
            period_from=dt.date(2026, 1, 1),
            period_to=dt.date(2026, 1, 31),
            full_days=20,
            half_days=2,
            ot_hours=D(3),
            advance_deduction=D(1000),
            status=payroll.default_status(),
        )
        self.assertEqual((p.days_worked, p.basic_amount), (D(21), D(10500)))
        self.assertEqual(p.net_amount, D(10500) + D(210) - D(1000))


class WageHistoryTests(Base):
    """Picker: 500 in 2025, 600 from Jan 2026, 650 from Jul 2026."""

    def setUp(self):
        super().setUp()
        self.job.default_daily_wage, self.job.default_ot_rate = D(999), D(99)
        self.job.save()
        for start, rate, ot in (
            (dt.date(2025, 1, 1), 500, 60),
            (dt.date(2026, 1, 1), 600, 70),
            (dt.date(2026, 7, 1), 650, 75),
        ):
            m.WageRate.objects.create(job_type=self.job, effective_from=start, rate=D(rate), ot_rate_per_hour=D(ot))

    def rates(self):
        return list(m.WageRate.objects.filter(employment_type=None).order_by("effective_from"))

    def test_open_rates_close_automatically(self):
        r2025, r2026, rmid = self.rates()
        self.assertEqual(
            (r2025.effective_to, r2026.effective_to, rmid.effective_to),
            (dt.date(2025, 12, 31), dt.date(2026, 6, 30), None),
        )

    def test_deleting_a_rate_reopens_the_previous_one(self):
        self.rates()[-1].delete()
        self.assertIsNone(self.rates()[-1].effective_to)
        self.assertEqual(self.job.wages_on(dt.date(2026, 9, 1)), (D(600), D(70)))

    def test_moving_a_start_date_moves_the_previous_end(self):
        rmid = self.rates()[-1]
        rmid.effective_from = dt.date(2026, 8, 1)
        rmid.save()
        self.assertEqual(self.rates()[1].effective_to, dt.date(2026, 7, 31))

    def test_rate_on_date(self):
        self.assertEqual(self.job.wages_on(dt.date(2025, 5, 1)), (D(500), D(60)))
        self.assertEqual(self.job.wages_on(dt.date(2026, 3, 1)), (D(600), D(70)))
        self.assertEqual(self.job.wages_on(dt.date(2026, 9, 1)), (D(650), D(75)))
        self.assertEqual(self.job.wages_on(dt.date(2024, 5, 1)), (D(999), D(99)))  # before history -> default

    def test_employee_type_rate_wins(self):
        m.WageRate.objects.create(
            job_type=self.job, employment_type=self.other_type, effective_from=dt.date(2026, 1, 1), rate=D(700)
        )
        day = dt.date(2026, 3, 1)
        other, permanent = (
            m.WorkContext(employment_type_id=self.other_type.pk),
            m.WorkContext(employment_type_id=self.permanent.pk),
        )
        self.assertEqual(self.job.wages_on(day, other), (D(700), D(99)))  # OT 0 -> job default
        self.assertEqual(self.job.wages_on(day, permanent), (D(600), D(70)))
        book = m.WageBook(day, day)
        self.assertEqual(book.wages_on(self.job.pk, day, other), (D(700), D(99)))
        count = m.GroupWorkEntry.objects.create(job_type=self.job, date=day, workers_count=2)
        self.assertEqual(count.wage_per_worker, D(600))  # employee types never apply to labour counts

    def test_rates_for_a_plot_an_employee_or_the_labour_count(self):
        """Cardamom picking on plot Thukkupalam pays 800 from March - for employees and the labour count
        alike; one employee gets 900 everywhere; the labour count elsewhere 550; everybody else 600."""
        thukku = m.Block.objects.create(name="Thukkupalam")
        other_plot = m.Block.objects.create(name="Other plot")
        ravi = m.Employee.objects.create(full_name="Ravi", job_type=self.job, employment_type=self.permanent)
        mar = dt.date(2026, 3, 2)
        # entered before the rates exist: they follow the list later; one wage was typed by hand
        e_thukku = m.WorkEntry.objects.create(employee=self.emp, job_type=self.job, date=mar, block=thukku)
        e_typed = m.WorkEntry.objects.create(employee=self.emp, job_type=self.job, date=mar, block=thukku, wage=D(777))
        c_thukku = m.GroupWorkEntry.objects.create(job_type=self.job, date=mar, block=thukku, workers_count=10)
        c_other = m.GroupWorkEntry.objects.create(job_type=self.job, date=mar, block=other_plot, workers_count=10)
        e_ravi = m.WorkEntry.objects.create(employee=ravi, job_type=self.job, date=mar, block=thukku)
        self.assertEqual((e_thukku.wage, e_typed.wage, c_thukku.wage_per_worker), (D(600), D(777), D(600)))
        self.assertEqual((e_typed.wage_auto, e_thukku.wage_auto), (False, True))

        start = dt.date(2026, 3, 1)
        m.WageRate.objects.create(job_type=self.job, block=thukku, effective_from=start, rate=D(800))
        m.WageRate.objects.create(job_type=self.job, employee=ravi, effective_from=start, rate=D(900))
        m.WageRate.objects.create(job_type=self.job, workers="count", effective_from=start, rate=D(550))
        for x in (e_thukku, e_typed, c_thukku, c_other, e_ravi):
            x.refresh_from_db()
        self.assertEqual(e_thukku.wage, D(800))  # picking on Thukkupalam
        self.assertEqual(c_thukku.wage_per_worker, D(800))  # the labour count there too (plot beats count-only)
        self.assertEqual(c_thukku.total_wages, D(8000))
        self.assertEqual(c_other.wage_per_worker, D(550))  # labour count elsewhere
        self.assertEqual(e_ravi.wage, D(900))  # the employee's own rate beats the plot
        self.assertEqual(e_typed.wage, D(777))  # typed by hand: kept
        elsewhere = m.WorkEntry.objects.create(employee=self.emp, job_type=self.job, date=mar, block=other_plot)
        self.assertEqual(elsewhere.wage, D(600))  # everybody else: the general rate
        before = m.WorkEntry.objects.create(
            employee=self.emp, job_type=self.job, date=dt.date(2026, 2, 27), block=thukku
        )
        self.assertEqual(before.wage, D(600))  # before the plot rate started

        # payroll: an attendance-only day on the plot gets the plot rate too
        day = dt.date(2026, 3, 3)
        a = self.attend(day)
        a.block = thukku
        a.save()
        line = payroll.calculate(self.emp, day, day)["lines"][0]
        self.assertEqual((line["rate"], line["source"]), ("800.00", "job"))

        # rates for different work may overlap; the same work may not
        with self.assertRaises(ValidationError):
            m.WageRate(
                job_type=self.job,
                block=thukku,
                effective_from=dt.date(2026, 2, 1),
                effective_to=dt.date(2026, 3, 5),
                rate=D(1),
            ).full_clean()
        m.WageRate(job_type=self.job, block=other_plot, effective_from=start, rate=D(820)).full_clean()
        with self.assertRaises(ValidationError):  # one employee is never in a labour count
            m.WageRate(job_type=self.job, employee=ravi, workers="count", effective_from=start, rate=D(1)).full_clean()

        # deleting the plot rate: its work goes back to the general rate
        m.WageRate.objects.get(block=thukku).delete()
        e_thukku.refresh_from_db()
        c_thukku.refresh_from_db()
        self.assertEqual((e_thukku.wage, c_thukku.wage_per_worker), (D(600), D(550)))

    def test_entries_use_rate_of_their_date(self):
        g25 = m.GroupWorkEntry.objects.create(job_type=self.job, date=dt.date(2025, 10, 1), workers_count=10)
        g26 = m.GroupWorkEntry.objects.create(job_type=self.job, date=dt.date(2026, 8, 1), workers_count=10)
        self.assertEqual((g25.total_wages, g26.total_wages), (D(5000), D(6500)))
        w = m.WorkEntry.objects.create(employee=self.emp, job_type=self.job, date=dt.date(2026, 2, 1), ot_hours=D(1))
        self.assertEqual((w.wage, w.ot_rate), (D(600), D(70)))

    def test_overlap_is_rejected(self):
        bad = m.WageRate(
            job_type=self.job, effective_from=dt.date(2025, 6, 1), effective_to=dt.date(2026, 2, 1), rate=D(1)
        )
        with self.assertRaises(ValidationError):
            bad.full_clean()


class PayrollTests(WageHistoryTests):
    def test_payroll_uses_rate_of_each_day(self):
        for d in range(25, 31):
            self.attend(dt.date(2026, 6, d))
        for d in range(1, 5):
            self.attend(dt.date(2026, 7, d), ot=2)
        c = payroll.calculate(self.emp, dt.date(2026, 6, 15), dt.date(2026, 7, 14))
        self.assertEqual((c["basic"], c["ot_amount"], len(c["lines"])), (D(6 * 600 + 4 * 650), D(8 * 75), 2))

    def test_priority_individual_then_job(self):
        """Day 1: individual entry 800 | Day 2: job type 650 even if a labour count says 700 | Day 3: 650 |
        Day 4: half day. Labour counts are for unregistered workers and never change an employee's pay."""
        days = [dt.date(2026, 8, d) for d in (3, 4, 5, 6)]
        for d in days[:3]:
            self.attend(d, ot=1)
        self.attend(days[3], status=self.half)
        m.WorkEntry.objects.create(
            employee=self.emp, job_type=self.job, date=days[0], wage=D(800), ot_rate=D(100), ot_hours=D(2)
        )
        m.GroupWorkEntry.objects.create(
            job_type=self.job, date=days[1], workers_count=8, wage_per_worker=D(700), ot_rate_per_hour=D(90)
        )
        c = payroll.calculate(self.emp, days[0], days[-1])
        self.assertEqual((c["full"], c["half"]), (3, 1))
        self.assertEqual(c["basic"], D(800 + 650 + 650 + 325))
        self.assertEqual(c["ot_amount"], D(2 * 100 + 1 * 75 + 1 * 75))  # work entry OT hours win on day 1
        self.assertEqual({x["source"] for x in c["lines"]}, {"individual", "job"})
        p = payroll.save(self.emp, days[0], days[-1], c)
        self.assertEqual((p.basic_amount, p.net_amount, p.days_worked), (c["basic"], c["net"], D("3.5")))
        p.bonus = D(100)
        p.save()
        self.assertEqual(p.net_amount, c["net"] + D(100))

    def test_labour_count_paid_per_gang_leader(self):
        murugan = m.GangLeader.objects.create(name="Murugan")
        selvi = m.GangLeader.objects.create(name="Selvi")
        aug = (dt.date(2026, 8, 1), dt.date(2026, 8, 31))
        # 10 pickers on two days with Murugan (2 of them 2 h OT in the evening), 6 with Selvi, 4 with nobody named
        for d in (3, 4):
            m.GroupWorkEntry.objects.create(
                job_type=self.job,
                gang_leader=murugan,
                date=dt.date(2026, 8, d),
                workers_count=10,
                ot_evening_workers=2,
                ot_evening_hours=D(2),
            )
        m.GroupWorkEntry.objects.create(
            job_type=self.job, gang_leader=selvi, date=dt.date(2026, 8, 3), workers_count=6, wage_per_worker=D(700)
        )
        m.GroupWorkEntry.objects.create(job_type=self.job, date=dt.date(2026, 8, 5), workers_count=4)
        counts = payroll.calculate_counts(*aug)
        self.assertEqual(set(counts), {murugan.pk, selvi.pk, None})
        c = counts[murugan.pk]
        self.assertEqual((c["full"], c["basic"], c["ot"], c["ot_amount"]), (20, D(20 * 650), D(8), D(8 * 75)))
        line = c["lines"][0]
        self.assertEqual((line["source"], line["job"], line["entries"]), ("count", self.job.name, 2))
        self.assertEqual(counts[selvi.pk]["basic"], D(6 * 700))
        p = payroll.save(None, *aug, c, gang_leader=murugan)
        self.assertTrue(p.is_labour_count)
        self.assertEqual((p.payee_name, p.days_worked, p.net_amount), ("Murugan", D(20), D(13600)))
        self.assertIn("Daily labour count", p.remarks)
        nobody = payroll.save(None, *aug, counts[None])
        self.assertEqual((nobody.gang_leader, nobody.basic_amount), (None, D(4 * 650)))

    def test_several_work_entries_on_one_day_are_added(self):
        day = dt.date(2026, 8, 3)
        for hours in (1, 2):
            m.WorkEntry.objects.create(
                employee=self.emp, job_type=self.job, date=day, wage=D(300), ot_rate=D(50), ot_hours=D(hours)
            )
        c = payroll.calculate(self.emp, day, day)
        self.assertEqual((c["basic"], c["ot_amount"]), (D(600), D(150)))

    def test_any_pay_fraction(self):
        three_quarter = m.AttendanceStatus.objects.create(name="Three quarter", pay_fraction=D("0.75"))
        for d in (3, 4):
            self.attend(dt.date(2026, 8, d))
        self.attend(dt.date(2026, 8, 5), status=three_quarter)
        c = payroll.calculate(self.emp, dt.date(2026, 8, 1), dt.date(2026, 8, 31))
        p = payroll.save(self.emp, dt.date(2026, 8, 1), dt.date(2026, 8, 31), c)
        self.assertEqual((p.days_worked, p.basic_amount), (D("2.75"), D("1787.50")))

    def test_context_gives_same_result(self):
        for d in range(1, 10):
            self.attend(dt.date(2026, 6, d), ot=1)
        d1, d2 = dt.date(2026, 6, 1), dt.date(2026, 6, 30)
        self.assertEqual(
            payroll.calculate(self.emp, d1, d2), payroll.calculate(self.emp, d1, d2, payroll.PayrollContext(d1, d2))
        )


class PageTests(TestCase):
    databases = {DEFAULT_DB, DEMO_DB}

    def setUp(self):
        self.admin = get_user_model().objects.create_superuser("admin", "admin@example.com", "pw")
        self.client.force_login(self.admin)
        self.job = m.JobType.objects.create(name="Test job", default_daily_wage=D(500), default_ot_rate=D(70))
        self.emp = m.Employee.objects.create(
            full_name="Ravi",
            job_type=self.job,
            date_of_joining=dt.date(2025, 1, 1),
            employment_type=lookup(m.EmploymentType),
        )

    def test_login_required(self):
        self.client.logout()
        self.assertEqual(self.client.get("/").status_code, 302)

    def test_pages_load(self):
        from .registry import MODULES

        urls = [
            "/",
            "/reports/analysis/",
            "/reports/builder/?ds=harvests&group=date&aggs=sum:quantity&run=1",
            "/m/attendance/daily/",
            "/m/payroll/generate/",
            "/m/lists/",
            "/settings/labels/",
            "/settings/demo/",
            f"/m/employees/{self.emp.pk}/profile/",
        ]
        for cfg in MODULES:
            urls += [f"/m/{cfg.slug}/"] + ([f"/m/{cfg.slug}/add/"] if cfg.can_add else [])
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_chemical_page_purchases_and_field_work_chemicals(self):
        kg = lookup(m.Unit)
        r = self.client.post(
            "/m/agri-inputs/add/",
            {
                "name": "Form NPK",
                "unit": kg.pk,
                "pack_size": "25",
                "dose_qty": "2.5",
                "dose_basis": "water",
                "dose_water": "500",
                "is_active": "on",
                "buy-TOTAL_FORMS": "2",
                "buy-INITIAL_FORMS": "0",
                "buy-0-date": "05/01/26",
                "buy-0-bags": "10",
                "buy-0-pack_size": "",
                "buy-0-price_per_bag": "250",
                "buy-0-supplier": "Agro",
                "buy-1-date": "05/03/26",
                "buy-1-bags": "2",
                "buy-1-pack_size": "25",
                "buy-1-price_per_bag": "450",
            },
        )
        self.assertEqual(r.status_code, 302, r.content.decode()[:3000])
        npk = m.AgriInput.objects.get(name="Form NPK")
        self.assertEqual((npk.purchased_qty, npk.stock_qty, npk.unit_cost), (D(300), D(300), D(18)))
        jan = npk.purchases.get(date=dt.date(2026, 1, 5))
        self.assertEqual((jan.pack_size, jan.unit_cost), (D(25), D(10)))
        page = self.client.get(f"/m/agri-inputs/{npk.pk}/edit/").content.decode()
        self.assertIn('id="buyRows"', page)

        op_type = lookup(m.OperationType)
        data = {
            "date": "01/02/26",
            "operation_type": op_type.pk,
            "mix_type": "water",
            "tank_litres": "500",
            "rounds": "4",
            "mix_name": "NPK spray",
            "area_covered_acres": "0",
            "chem-TOTAL_FORMS": "1",
            "chem-INITIAL_FORMS": "0",
            "chem-0-agri_input": npk.pk,
            "chem-0-purchase": jan.pk,
            "chem-0-dose_qty": "2.5",
            "chem-0-dose_basis": "water",
            "chem-0-dose_water": "500",
            "chem-0-quantity": "",
            "chem-0-unit_cost": "",
        }
        page = self.client.get("/m/field-operations/add/").content.decode()
        self.assertIn('id="chemSearch"', page)
        self.assertIn("Form NPK", page)
        r = self.client.post("/m/field-operations/add/", data)
        self.assertEqual(r.status_code, 302, r.content.decode()[:3000])
        op = m.FieldOperation.objects.latest("id")
        self.assertEqual((op.rounds, op.input_cost), (D(4), D(100)))  # 2.5 kg x 4 mixes at the January price
        line = op.chemicals.get()
        self.assertEqual((line.quantity, line.unit_cost), (D(10), D(10)))
        # the mix can be picked again on a new entry
        self.assertIn("NPK spray", self.client.get("/m/field-operations/add/").content.decode())
        # editing: the line's quantity changes, its price stays; a purchase of another chemical is refused
        edit = {
            **data,
            "chem-INITIAL_FORMS": "1",
            "chem-0-id": line.pk,
            "chem-0-operation": op.pk,
            "chem-0-quantity": "12",
            "chem-0-unit_cost": "10",
        }
        self.assertEqual(self.client.post(f"/m/field-operations/{op.pk}/edit/", edit).status_code, 302)
        op.refresh_from_db()
        self.assertEqual(op.input_cost, D(120))
        other = m.AgriInput.objects.create(name="Other", unit=kg)
        bad = m.AgriInputPurchase.objects.create(agri_input=other, bags=D(1), price_per_bag=D(5))
        r = self.client.post(f"/m/field-operations/{op.pk}/edit/", {**edit, "chem-0-purchase": bad.pk})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "This purchase is of another fertilizer / chemical.")
        # the line's edit link goes to its field-work entry; the list of chemicals used opens
        r = self.client.get(f"/m/chemical-use/{line.pk}/edit/")
        self.assertRedirects(r, f"/m/field-operations/{op.pk}/edit/", fetch_redirect_response=False)
        self.assertContains(self.client.get("/m/chemical-use/"), "Form NPK")

    def test_exports(self):
        m.Employee.objects.create(
            full_name="=HYPERLINK(1)", job_type=self.job, employment_type=lookup(m.EmploymentType)
        )
        csv = self.client.get("/m/employees/export/")
        self.assertEqual(csv["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("'=HYPERLINK(1)", b"".join(csv.streaming_content).decode("utf-8-sig"))
        for url in (
            "/m/employees/pdf/",
            "/reports/analysis/?export=pdf",
            "/reports/builder/?ds=employees&run=1&export=pdf",
        ):
            resp = self.client.get(url)
            self.assertEqual((resp.status_code, resp["Content-Type"]), (200, "application/pdf"), url)
            self.assertTrue(resp.content.startswith(b"%PDF"))

    def test_date_boxes_take_dd_mm_yy(self):
        resp = self.client.post(
            "/m/harvests/add/",
            {
                "date": "05/09/26",
                "product": m.Product.objects.create(name="Test date crop", unit=lookup(m.Unit)).pk,
                "quantity": "12",
            },
        )
        h = m.Harvest.objects.last()
        self.assertIsNotNone(h, resp.content.decode()[-3000:])
        self.assertEqual(h.date, dt.date(2026, 9, 5))
        page = self.client.get(f"/m/harvests/{h.pk}/edit/").content.decode()
        self.assertIn('value="05/09/26"', page)
        listing = self.client.get("/m/harvests/?date_from=01/09/26&date_to=30/09/26").content.decode()
        self.assertIn("05/09/26", listing)
        self.assertIn('value="01/09/26"', listing)

    def test_harvest_form_live_preview(self):
        picking = m.JobType.objects.create(name="Test picking job", default_daily_wage=D(500))
        crop = m.Product.objects.create(name="Preview crop", harvest_job_type=picking)
        day = dt.date(2026, 10, 5)
        m.GroupWorkEntry.objects.create(
            job_type=picking, date=day, workers_count=8, ot_evening_workers=2, ot_evening_hours=2
        )
        m.WorkEntry.objects.create(employee=self.emp, job_type=picking, date=day, ot_hours=2)
        h = m.Harvest.objects.create(product=crop, date=day, quantity=50)
        data = self.client.get(f"/m/harvests/people/?date=05/10/26&product={crop.pk}").json()
        self.assertEqual(
            (data["ind_people"], data["count_people"], data["ind_ot_hours"], data["count_ot_hours"], data["hours"]),
            (1, 8, 2, 4, 8),
        )
        self.assertEqual((data["other_quantity"], data["job"]), (50, "Test picking job"))
        data = self.client.get(f"/m/harvests/people/?date=2026-10-05&job={picking.pk}&pk={h.pk}").json()
        self.assertEqual(data["other_quantity"], 0)  # the harvest being edited is not counted twice
        self.assertFalse(self.client.get("/m/harvests/people/?date=bad").json()["ok"])
        page = self.client.get("/m/harvests/add/").content.decode()
        self.assertIn('id="harvestPreview"', page)

    def test_attendance_calendar(self):
        full = lookup(m.AttendanceStatus, pay_fraction=1)
        half = lookup(m.AttendanceStatus, pay_fraction=D("0.5"))
        m.Attendance.objects.create(employee=self.emp, date=dt.date(2026, 3, 2), status=full)
        m.Attendance.objects.create(employee=self.emp, date=dt.date(2026, 3, 3), status=half)
        page = self.client.get("/m/attendance/calendar/?month=2026-03").content.decode()
        self.assertIn("Ravi", page)
        self.assertIn(f"background:{full.color}", page)
        single = self.client.get(f"/m/attendance/calendar/?month=2026-03&employee={self.emp.pk}")
        self.assertEqual(single.context["people"][0]["paid"], D("1.5"))
        self.assertTrue(single.context["single"])
        self.assertEqual(self.client.get("/m/attendance/calendar/?month=bad").status_code, 200)
        self.assertIn("/m/attendance/calendar/", self.client.get("/m/attendance/").content.decode())  # the tab

    def test_work_entries_for_several_employees(self):
        other_job = m.JobType.objects.create(name="Other job", default_daily_wage=D(600), default_ot_rate=D(80))
        second = m.Employee.objects.create(
            full_name="Second", job_type=other_job, employment_type=lookup(m.EmploymentType)
        )
        block = m.Block.objects.create(name="Test block")
        resp = self.client.post(
            "/m/work-entries/add/",
            {"employees": [self.emp.pk, second.pk], "date": "06/10/26", "block": block.pk, "wage": "999"},
        )
        self.assertEqual(resp.status_code, 302)
        rows = {w.employee_id: w for w in m.WorkEntry.objects.filter(date=dt.date(2026, 10, 6))}
        self.assertEqual(len(rows), 2)
        self.assertEqual((rows[self.emp.pk].job_type, rows[self.emp.pk].wage), (self.job, D(500)))  # own job's wage
        self.assertEqual(
            (rows[second.pk].job_type, rows[second.pk].wage, rows[second.pk].ot_rate), (other_job, D(600), D(80))
        )
        self.assertTrue(all(w.block == block and w.created_by == self.admin for w in rows.values()))
        # one employee: the normal form, typed values are kept
        self.client.post(
            "/m/work-entries/add/",
            {
                "employees": [second.pk],
                "date": "07/10/26",
                "job_type": other_job.pk,
                "wage": "650",
                "ot_rate": "80",
                "ot_hours": "1",
                "output_quantity": "0",
            },
        )
        w = m.WorkEntry.objects.get(date=dt.date(2026, 10, 7))
        self.assertEqual((w.employee, w.wage), (second, D(650)))
        self.assertIn('"multi"', self.client.get("/m/work-entries/add/").content.decode())

    def _payroll(self, net, d1=dt.date(2026, 9, 1), d2=dt.date(2026, 9, 30)):
        return m.SalaryPayment.objects.create(
            employee=self.emp,
            period_from=d1,
            period_to=d2,
            full_days=net / 1000,
            daily_rate=1000,
            ot_rate=1,
            status=lookup(m.PaymentStatus, is_final=False),
        )

    def _pay(self, p, amount, day, **changes):
        """Save the payroll's edit page with a current payment amount (and any other changes)."""
        p.refresh_from_db()
        data = {
            "employee": p.employee_id,
            "period_from": p.period_from.isoformat(),
            "period_to": p.period_to.isoformat(),
            "full_days": p.full_days,
            "half_days": p.half_days,
            "leave_days": p.leave_days,
            "daily_rate": p.daily_rate,
            "ot_hours": p.ot_hours,
            "ot_rate": p.ot_rate,
            "bonus": p.bonus,
            "advance_deduction": p.advance_deduction,
            "other_deduction": p.other_deduction,
            "status": p.status_id,
            "remarks": "",
            "payment_date": day,
            "current_payment": amount,
            **changes,
        }
        return self.client.post(f"/m/salary-payments/{p.pk}/edit/", {k: v for k, v in data.items() if v is not None})

    def test_generate_payroll_for_one_employee(self):
        job = m.JobType.objects.create(name="Gen job", default_daily_wage=D(500), default_ot_rate=D(50))
        full = lookup(m.AttendanceStatus, pay_fraction=1)
        a = m.Employee.objects.create(full_name="Anu", job_type=job, employment_type=lookup(m.EmploymentType))
        b = m.Employee.objects.create(full_name="Babu", job_type=job, employment_type=lookup(m.EmploymentType))
        for emp in (a, b):
            m.Attendance.objects.create(employee=emp, date=dt.date(2026, 9, 2), status=full)
        m.GroupWorkEntry.objects.create(job_type=job, date=dt.date(2026, 9, 2), workers_count=5)
        page = self.client.get(f"/m/payroll/generate/?employee={a.pk}").content.decode()
        self.assertRegex(page, rf'name="employee" value="{a.pk}" id="[^"]+" checked')
        data = {
            "date_from": "01/09/26",
            "date_to": "30/09/26",
            "employee": a.pk,
            "include_employees": "on",
            "include_counts": "on",
            "include_pending": "on",
        }
        preview = self.client.post("/m/payroll/generate/", {**data, "preview": "1"}).context["preview"]
        self.assertEqual([r["emp"].pk for r in preview], [a.pk])  # only Anu, no labour count
        self.client.post("/m/payroll/generate/", {**data, "confirm": "1"})
        self.assertEqual(list(m.SalaryPayment.objects.values_list("employee_id", flat=True)), [a.pk])

        # several employees; only the ticked rows of the preview are created
        c = m.Employee.objects.create(full_name="Chinnu", job_type=job, employment_type=lookup(m.EmploymentType))
        m.Attendance.objects.create(employee=c, date=dt.date(2026, 9, 3), status=full)
        data["employee"] = [a.pk, b.pk, c.pk]
        preview = self.client.post("/m/payroll/generate/", {**data, "preview": "1"}).context["preview"]
        self.assertEqual({r["emp"].pk: r["status"] for r in preview}, {a.pk: "exists", b.pk: "new", c.pk: "new"})
        anu = next(r for r in preview if r["emp"].pk == a.pk)
        self.assertEqual([p.employee_id for p in anu["existing"]], [a.pk])
        page = self.client.post("/m/payroll/generate/", {**data, "preview": "1"}).content.decode()
        self.assertIn(f"/m/salary-payments/{anu['existing'][0].pk}/edit/", page)  # opens the existing payroll
        self.client.post("/m/payroll/generate/", {**data, "confirm": "1", "picking": "1", "pick": [f"e{c.pk}"]})
        self.assertEqual(
            sorted(m.SalaryPayment.objects.values_list("employee_id", flat=True)), sorted([a.pk, c.pk])
        )  # Babu was not ticked

        # gang leaders: their Daily Labour Count, alone or together with employees
        leader = m.GangLeader.objects.create(name="Murugan")
        m.GroupWorkEntry.objects.create(job_type=job, date=dt.date(2026, 9, 4), workers_count=3, gang_leader=leader)
        only_leader = {**data, "employee": [], "gang_leader": [str(leader.pk)], "preview": "1"}
        preview = self.client.post("/m/payroll/generate/", only_leader).context["preview"]
        self.assertEqual([(r["emp"], r["leader"]) for r in preview], [(None, leader)])
        both = {**data, "employee": [b.pk], "gang_leader": [str(leader.pk), "0"], "preview": "1"}
        preview = self.client.post("/m/payroll/generate/", both).context["preview"]
        self.assertEqual(
            sorted((r["emp"].pk if r["emp"] else 0, r["leader"].pk if r["leader"] else 0) for r in preview),
            sorted([(b.pk, 0), (0, leader.pk), (0, 0)]),  # Babu, Murugan's count, the count without a leader
        )

    def test_generate_payroll_for_one_plot(self):
        job = m.JobType.objects.create(name="Plot job", default_daily_wage=D(500), default_ot_rate=D(50))
        full = lookup(m.AttendanceStatus, pay_fraction=1)
        t, u = m.Block.objects.create(name="Thukkupalam"), m.Block.objects.create(name="Upper")
        a = m.Employee.objects.create(full_name="Anu", job_type=job, employment_type=lookup(m.EmploymentType))
        b = m.Employee.objects.create(full_name="Babu", job_type=job, employment_type=lookup(m.EmploymentType))
        for day, blk in ((1, t), (2, t), (3, u)):
            m.Attendance.objects.create(employee=a, date=dt.date(2026, 9, day), status=full, block=blk)
        m.WorkEntry.objects.create(employee=b, job_type=job, date=dt.date(2026, 9, 4), block=u)
        leader = m.GangLeader.objects.create(name="Kangani")
        m.GroupWorkEntry.objects.create(
            job_type=job, date=dt.date(2026, 9, 1), workers_count=4, gang_leader=leader, block=t
        )
        m.GroupWorkEntry.objects.create(
            job_type=job, date=dt.date(2026, 9, 2), workers_count=6, gang_leader=leader, block=u
        )
        data = {
            "date_from": "01/09/26",
            "date_to": "30/09/26",
            "include_employees": "on",
            "include_counts": "on",
            "blocks": [t.pk],
        }
        preview = self.client.post("/m/payroll/generate/", {**data, "preview": "1"}).context["preview"]
        rows = {(r["emp"].pk if r["emp"] else "count"): r for r in preview}
        self.assertEqual(set(rows), {a.pk, "count"})  # Babu worked only on the other plot
        self.assertEqual((rows[a.pk]["full"], rows["count"]["full"]), (2, D(4)))  # Thukkupalam days / man-days
        self.client.post("/m/payroll/generate/", {**data, "confirm": "1"})
        self.assertEqual(
            sorted(m.SalaryPayment.objects.values_list("blocks__name", "days_worked")),
            sorted([("Thukkupalam", D(2)), ("Thukkupalam", D(4))]),
        )
        # the other plot is paid separately for the same period: no clash with Thukkupalam
        preview = self.client.post("/m/payroll/generate/", {**data, "blocks": [u.pk], "preview": "1"}).context[
            "preview"
        ]
        self.assertEqual({r["status"] for r in preview}, {"new"})
        self.client.post("/m/payroll/generate/", {**data, "blocks": [u.pk], "confirm": "1"})
        self.assertEqual(m.SalaryPayment.objects.filter(blocks=u).count(), 3)  # Anu, Babu, the count
        # all plots for the same period now overlaps those payrolls: nothing paid twice
        all_plots = {**data, "blocks": [], "preview": "1"}
        preview = self.client.post("/m/payroll/generate/", all_plots).context["preview"]
        self.assertEqual({r["status"] for r in preview}, {"exists"})
        self.assertIn(
            "Thukkupalam",
            self.client.get(
                f"/m/payroll/{m.SalaryPayment.objects.filter(blocks=t).first().pk}/payslip/"
            ).content.decode(),
        )
        # both plots together also overlaps the single-plot payrolls
        preview = self.client.post("/m/payroll/generate/", {**data, "blocks": [t.pk, u.pk], "preview": "1"})
        self.assertEqual({r["status"] for r in preview.context["preview"]}, {"exists"})
        # the payroll list filtered by a plot shows only that plot's payrolls
        listing = self.client.get(f"/m/salary-payments/?blocks={t.pk}")
        self.assertEqual(listing.context["count"], 2)
        # the edit page shows the plots (fixed: they come from Generate Payroll)
        edit = self.client.get(f"/m/salary-payments/{m.SalaryPayment.objects.filter(blocks=u).first().pk}/edit/")
        self.assertIn('class="chip-pick"', edit.content.decode())

    def test_recalculate_replaces_overlapping_unpaid_payroll(self):
        job = m.JobType.objects.create(name="Re job", default_daily_wage=D(500), default_ot_rate=D(50))
        full = lookup(m.AttendanceStatus, pay_fraction=1)
        a = m.Employee.objects.create(full_name="Anu", job_type=job, employment_type=lookup(m.EmploymentType))
        for day in (1, 10, 20):
            m.Attendance.objects.create(employee=a, date=dt.date(2026, 9, day), status=full)
        data = {"date_from": "01/09/26", "date_to": "15/09/26", "include_employees": "on"}
        self.client.post("/m/payroll/generate/", {**data, "confirm": "1"})
        first = m.SalaryPayment.objects.get()
        self.assertEqual(first.days_worked, D(2))
        whole = {**data, "date_to": "30/09/26"}
        # not ticked: skipped, and the page says why
        page = self.client.post("/m/payroll/generate/", {**whole, "preview": "1"})
        self.assertEqual(page.context["preview"][0]["why"], "tick")
        # ticked: the unpaid payroll of 01-15/09 is re-calculated for 01-30/09 (no second payroll)
        self.client.post("/m/payroll/generate/", {**whole, "overwrite": "on", "confirm": "1"})
        p = m.SalaryPayment.objects.get()
        self.assertEqual((p.pk, p.period_to, p.days_worked), (first.pk, dt.date(2026, 9, 30), D(3)))
        # two unpaid payrolls overlapping: one is re-used, the other replaced
        m.SalaryPayment.objects.create(
            employee=a,
            period_from=dt.date(2026, 9, 25),
            period_to=dt.date(2026, 10, 5),
            full_days=1,
            daily_rate=500,
            ot_rate=1,
            status=lookup(m.PaymentStatus, is_final=False),
        )
        month = {**data, "date_to": "05/10/26", "overwrite": "on"}
        preview = self.client.post("/m/payroll/generate/", {**month, "preview": "1"}).context["preview"]
        self.assertEqual((preview[0]["status"], preview[0]["replaces"]), ("update", 2))
        self.client.post("/m/payroll/generate/", {**month, "confirm": "1"})
        self.assertEqual(m.SalaryPayment.objects.count(), 1)
        # once money is paid it is never re-calculated
        p = m.SalaryPayment.objects.get()
        m.SalaryPaid.objects.create(salary=p, date=dt.date(2026, 10, 6), amount=D(100))
        preview = self.client.post("/m/payroll/generate/", {**month, "preview": "1"}).context["preview"]
        self.assertEqual((preview[0]["status"], preview[0]["why"]), ("exists", "paid"))

    def test_plot_payroll_takes_only_its_plots_pending(self):
        job = m.JobType.objects.create(name="Pend job", default_daily_wage=D(500), default_ot_rate=D(50))
        full = lookup(m.AttendanceStatus, pay_fraction=1)
        t, u = m.Block.objects.create(name="T plot"), m.Block.objects.create(name="U plot")
        a = m.Employee.objects.create(full_name="Anu", job_type=job, employment_type=lookup(m.EmploymentType))
        status = lookup(m.PaymentStatus, is_final=False)
        for blk, net in ((t, 1000), (u, 3000)):  # unpaid August payrolls, one per plot
            old = m.SalaryPayment.objects.create(
                employee=a,
                period_from=dt.date(2026, 8, 1),
                period_to=dt.date(2026, 8, 31),
                full_days=D(net) / 500,
                daily_rate=500,
                ot_rate=1,
                status=status,
            )
            old.blocks.set([blk])
        m.Attendance.objects.create(employee=a, date=dt.date(2026, 9, 1), status=full, block=t)
        data = {"date_from": "01/09/26", "date_to": "30/09/26", "include_employees": "on", "include_pending": "on"}
        preview = self.client.post("/m/payroll/generate/", {**data, "blocks": [t.pk], "preview": "1"})
        self.assertEqual(preview.context["preview"][0]["pending"], D(1000))  # only plot T's balance

    def test_delete_shows_and_removes_linked_records(self):
        p = self._payroll(D(73000))
        m.SalaryPaid.objects.create(salary=p, date=dt.date(2026, 10, 1), amount=D(50000))
        m.SalaryPaid.objects.create(salary=p, date=dt.date(2026, 10, 2), amount=D(10000))
        url = f"/m/salary-payments/{p.pk}/delete/"
        preview = self.client.get(url + "?preview=1").json()
        self.assertTrue(preview["can_delete"])
        paid = [g for g in preview["groups"] if g["action"] == "delete"]
        self.assertEqual((len(paid), paid[0]["count"]), (1, 2))  # its 2 payments made
        resp = self.client.post(url, {"next": "/m/salary-payments/"})
        self.assertRedirects(resp, "/m/salary-payments/", fetch_redirect_response=False)
        self.assertFalse(m.SalaryPayment.objects.filter(pk=p.pk).exists())
        self.assertEqual(m.SalaryPaid.objects.count(), 0)

        # a sale with payments received goes together with them
        crop = m.Product.objects.create(name="Del crop")
        sale = m.Sale.objects.create(product=crop, quantity=D(10), rate=D(100), date=dt.date(2026, 10, 1))
        m.SaleReceipt.objects.create(sale=sale, date=dt.date(2026, 10, 2), amount=D(300))
        self.client.post(f"/m/sales/{sale.pk}/delete/")
        self.assertEqual((m.Sale.objects.count(), m.SaleReceipt.objects.count()), (0, 0))

        # a shared list entry still in use is never deleted with business records: the pop-up says why
        mode = lookup(m.PaymentStatus, is_final=False)
        self._payroll(D(1000))
        slug = next(c.slug for c in LOOKUP_MODULES if c.model is m.PaymentStatus)
        preview = self.client.get(f"/m/{slug}/{mode.pk}/delete/?preview=1").json()
        self.assertFalse(preview["can_delete"])
        self.assertEqual(preview["groups"][0]["action"], "block")
        self.client.post(f"/m/{slug}/{mode.pk}/delete/")
        self.assertTrue(m.PaymentStatus.objects.filter(pk=mode.pk).exists())
        self.assertEqual(m.SalaryPayment.objects.count(), 1)

        # an unused list entry: links elsewhere are only emptied
        block = m.Block.objects.create(name="Gone block")
        h = m.Harvest.objects.create(product=crop, block=block, quantity=D(5))
        preview = self.client.get(f"/m/blocks/{block.pk}/delete/?preview=1").json()
        self.assertTrue(preview["can_delete"])
        self.client.post(f"/m/blocks/{block.pk}/delete/")
        h.refresh_from_db()
        self.assertIsNone(h.block_id)
        page = self.client.get(f"/m/salary-payments/{m.SalaryPayment.objects.get().pk}/delete/").content.decode()
        self.assertIn("Yes, delete", page)

    def test_salary_paid_in_parts(self):
        p = self._payroll(D(73000))
        self.assertEqual((p.net_amount, p.balance, p.pay_state), (D(73000), D(73000), "unpaid"))
        page = self.client.get(f"/m/salary-payments/{p.pk}/edit/").content.decode()
        self.assertIn('name="current_payment"', page)
        self.assertIn('id="salaryLive"', page)
        # status 'Pending': the payment is recorded as a pending entry, not counted as paid
        resp = self._pay(p, "50000", "23/10/26")
        p.refresh_from_db()
        self.assertEqual((p.amount_paid, p.balance, p.pay_state), (D(0), D(73000), "unpaid"))
        first = p.payments.get()
        self.assertEqual((first.status, first.balance_after), ("pending", D(73000)))
        self.assertIn("Pending (not paid)", self.client.get(f"/m/payroll/payment/{first.pk}/").content.decode())
        self.assertIn("Pending (not paid)", self.client.get("/m/salary-paid/").content.decode())
        self.assertEqual(self.client.get("/m/salary-paid/?status=pending").context["count"], 1)
        # marked Paid later on its Edit: Salary paid page
        resp = self.client.post(
            f"/m/salary-paid/{first.pk}/edit/",
            {"salary": p.pk, "date": "23/10/26", "amount": "50000", "status": "paid", "remarks": ""},
        )
        self.assertEqual(resp.status_code, 302)
        p.refresh_from_db()
        self.assertEqual((p.amount_paid, p.balance, p.pay_state), (D(50000), D(23000), "part"))
        paid_status = lookup(m.PaymentStatus, is_final=True)
        self._pay(p, "10000", "24/10/26", status=paid_status.pk)  # status 'Paid': counted
        p.refresh_from_db()
        self.assertEqual((p.amount_paid, p.balance, p.pay_state), (D(60000), D(13000), "part"))
        self.assertEqual(p.payments.filter(status="paid").count(), 2)
        resp = self._pay(p, "20000", "25/10/26", bonus="5")  # more than pending: refused, nothing saved
        self.assertEqual(resp.status_code, 200)
        self.assertIn("pending", resp.content.decode())
        p.refresh_from_db()
        self.assertEqual((p.payments.count(), p.bonus), (2, D(0)))
        self._pay(p, "13000", "25/10/26")
        p.refresh_from_db()
        self.assertEqual((p.balance, p.pay_state, p.payment_date), (D(0), "paid", dt.date(2026, 10, 25)))
        self.assertTrue(p.status.is_final)
        paid = list(p.payments.order_by("date"))
        self.assertEqual([x.balance_after for x in paid], [D(23000), D(13000), D(0)])
        self.assertEqual(self.client.get("/m/salary-paid/add/").status_code, 302)  # paid from the payroll only
        edit = self.client.get(f"/m/salary-paid/{paid[0].pk}/edit/").content.decode()
        self.assertIn(f"/m/salary-payments/{p.pk}/edit/", edit)  # back to the payroll
        page = self.client.get(f"/m/payroll/payment/{paid[2].pk}/").content.decode()
        for x in paid:
            self.assertIn(f"/m/payroll/payment/{x.pk}/", page)
        self.assertIn(f"/m/payroll/{p.pk}/payslip/", page)
        self.assertIn("This payment", page)
        self.assertIn("23,000", self.client.get(f"/m/payroll/{p.pk}/payslip/").content.decode())
        self.assertIn(
            f"/m/payroll/payment/{paid[0].pk}/", self.client.get(f"/m/salary-payments/{p.pk}/edit/").content.decode()
        )
        # deleting a payment (from the pop-up) makes it pending again and returns to the page it came from
        listing = self.client.get("/m/salary-paid/").content.decode()
        self.assertIn(f'href="/m/salary-paid/{paid[2].pk}/delete/" data-confirm', listing)
        back = f"/m/salary-payments/{p.pk}/edit/"
        resp = self.client.post(f"/m/salary-paid/{paid[2].pk}/delete/", {"next": back})
        self.assertRedirects(resp, back, fetch_redirect_response=False)
        resp = self.client.post(f"/m/salary-paid/{paid[1].pk}/delete/", {"next": f"/m/payroll/payment/{paid[1].pk}/"})
        self.assertRedirects(resp, "/m/salary-paid/", fetch_redirect_response=False)  # its own page is gone
        m.SalaryPaid.objects.create(salary=p, date=dt.date(2026, 10, 24), amount=10000)
        p.refresh_from_db()
        self.assertEqual((p.balance, p.pay_state), (D(13000), "part"))  # 50,000 + 10,000 paid
        self.assertEqual(self.client.get("/m/salary-payments/?pay_state=pending").context["count"], 1)
        self.assertEqual(
            self.client.post(f"/m/salary-paid/{first.pk}/delete/", {"next": "https://evil.example/"}).url,
            "/m/salary-paid/",
        )

    def test_sale_received_in_parts(self):
        crop = m.Product.objects.create(name="Sale crop", unit=lookup(m.Unit))
        base = {
            "date": "01/10/26",
            "product": crop.pk,
            "quantity": "100",
            "rate": "1000",
            "buyer": "Trader",
            "remarks": "No auction",
        }
        resp = self.client.post("/m/sales/add/", {**base, "received_now": "30000", "received_date": "01/10/26"})
        self.assertEqual(resp.status_code, 302)
        sale = m.Sale.objects.get(product=crop)
        self.assertEqual((sale.total_amount, sale.amount_received, sale.balance), (D(100000), D(30000), D(70000)))
        self.assertEqual((sale.receipt_state, sale.payment_status.means), ("part", "part"))
        edit = f"/m/sales/{sale.pk}/edit/"
        self.client.post(edit, {**base, "received_now": "20000", "received_date": "08/10/26"})
        resp = self.client.post(edit, {**base, "received_now": "60000", "received_date": "09/10/26"})
        self.assertEqual(resp.status_code, 200)  # more than is still to receive: refused
        resp = self.client.post(edit, {**base, "received_now": "10", "received_date": "30/09/26"})
        self.assertEqual(resp.status_code, 200)  # before the sold date: refused
        self.client.post(edit, {**base, "received_now": "50000", "received_date": "09/10/26"})
        sale.refresh_from_db()
        self.assertEqual(
            (sale.balance, sale.receipt_state, sale.last_received_on), (D(0), "received", dt.date(2026, 10, 9))
        )
        receipts = list(sale.receipts.order_by("date"))
        self.assertEqual([r.balance_after for r in receipts], [D(70000), D(50000), D(0)])
        self.assertEqual([r.percent for r in receipts], [D(30), D(20), D(50)])
        page = self.client.get(edit).content.decode()
        self.assertIn('id="saleLive"', page)
        self.assertIn("When the money came in", page)
        for r in receipts:
            self.assertIn(f"/m/sale-receipts/{r.pk}/edit/", page)
        listing = self.client.get("/m/sales/").content.decode()
        self.assertIn("No auction", listing)
        self.client.post(f"/m/sale-receipts/{receipts[2].pk}/delete/")
        sale.refresh_from_db()
        self.assertEqual((sale.balance, sale.receipt_state), (D(50000), "part"))
        self.assertEqual(self.client.get("/m/sales/?receipt_state=open").context["count"], 1)

    def test_pending_balance_added_to_next_payroll(self):
        old = self._payroll(D(73000))
        m.SalaryPaid.objects.create(salary=old, date=dt.date(2026, 10, 3), amount=50000)
        full = lookup(m.AttendanceStatus, pay_fraction=1)
        for day in (1, 2):
            m.Attendance.objects.create(employee=self.emp, date=dt.date(2026, 10, day), status=full)
        data = {
            "date_from": "01/10/26",
            "date_to": "31/10/26",
            "include_employees": "on",
            "include_pending": "on",
            "confirm": "1",
        }
        self.client.post("/m/payroll/generate/", data)
        new = m.SalaryPayment.objects.get(period_from=dt.date(2026, 10, 1))
        old.refresh_from_db()
        self.assertEqual((new.previous_pending, new.net_amount), (D(23000), D(24000)))  # 2 days x 500 + 23,000
        self.assertEqual((old.balance, old.pay_state, old.carried_to), (D(0), "carried", new))
        self.assertIn(
            f"/m/payroll/{old.pk}/payslip/", self.client.get(f"/m/payroll/{new.pk}/payslip/").content.decode()
        )
        # paid payrolls are never re-calculated; deleting the new one makes the old balance pending again
        new.delete()
        old.refresh_from_db()
        self.assertEqual((old.balance, old.pay_state), (D(23000), "part"))

    def test_mark_daily_attendance(self):
        full = lookup(m.AttendanceStatus, pay_fraction=1)
        half = lookup(m.AttendanceStatus, pay_fraction=D("0.5"))
        day = dt.date(2026, 3, 2)
        post = {"date": day.isoformat(), f"status_{self.emp.pk}": full.pk, f"ot_{self.emp.pk}": "2"}
        self.client.post("/m/attendance/daily/", post)
        att = m.Attendance.objects.get(employee=self.emp, date=day)
        self.assertEqual((att.status, att.ot_hours, att.created_by), (full, D(2), self.admin))
        stamp = att.updated_at
        self.client.post("/m/attendance/daily/", post)  # nothing changed: no write
        self.assertEqual(m.Attendance.objects.get(pk=att.pk).updated_at, stamp)
        self.client.post(
            "/m/attendance/daily/", {**post, f"status_{self.emp.pk}": half.pk, f"remarks_{self.emp.pk}": "x"}
        )
        att.refresh_from_db()
        self.assertEqual((att.status, att.remarks, m.Attendance.objects.count()), (half, "x", 1))

    def test_pdf_text_is_the_same_with_the_quick_path(self):
        import re

        from core import pdf as pdf_module

        def make():
            p = pdf_module.new_pdf("ശമ്പളം Report", "हिन्दी")
            p.set_creation_date(dt.datetime(2026, 1, 1, tzinfo=dt.UTC))
            p.data_table(["A", "B"], [["Ravi 12-03-2026", "ஏலக்காய் 1,250.00"]] * 30, [False, True])
            return bytes(p.output())

        quick = make()
        saved, pdf_module.NEEDS_BIDI = pdf_module.NEEDS_BIDI, re.compile(".")
        try:
            self.assertEqual(quick, make())
        finally:
            pdf_module.NEEDS_BIDI = saved

    def test_bad_report_input_does_not_crash(self):
        for qs in (
            "ds=employees&run=1&f_field=employee_code&f_op=gt&f_val=x",
            "ds=harvests&run=1&f_field=quantity&f_op=in&f_val=abc",
            "ds=harvests&run=1&f_field=quantity&f_op=eq&f_val=NaN",
            "ds=employees&run=1&f_field=is_active&f_op=gt&f_val=1",
            "saved=abc",
            "ds=harvests&run=1&group=date&period=nope",
        ):
            self.assertIn(self.client.get(f"/reports/builder/?{qs}").status_code, (200, 302), qs)
        self.assertEqual(self.client.get("/reports/analysis/?date_from=0001-01-05").status_code, 200)

    def test_non_staff_cannot_change_lists_or_wages(self):
        user = get_user_model().objects.create_user("writer", password="pw")
        self.client.force_login(user)
        status = lookup(m.AttendanceStatus)
        self.assertEqual(self.client.get("/m/attendance-statuses/").status_code, 200)
        self.client.post(f"/m/attendance-statuses/{status.pk}/delete/")
        self.assertTrue(m.AttendanceStatus.objects.filter(pk=status.pk).exists())
        self.assertRedirects(self.client.get(f"/m/job-types/{self.job.pk}/edit/"), "/m/job-types/")
        self.assertEqual(self.client.get("/m/harvests/add/").status_code, 200)

    def test_generate_payroll_pays_employees_and_labour_counts(self):
        d1, d2 = dt.date(2026, 8, 1), dt.date(2026, 8, 31)
        leader = m.GangLeader.objects.create(name="Raju")
        m.Attendance.objects.create(
            employee=self.emp, date=dt.date(2026, 8, 3), status=lookup(m.AttendanceStatus, pay_fraction=1)
        )
        m.GroupWorkEntry.objects.create(
            job_type=self.job, gang_leader=leader, date=dt.date(2026, 8, 3), workers_count=10
        )
        post = {"date_from": d1, "date_to": d2, "include_employees": "on", "include_counts": "on"}
        preview = self.client.post("/m/payroll/generate/", post)
        self.assertContains(preview, "Raju")
        self.client.post("/m/payroll/generate/", {**post, "confirm": "1"})
        self.assertEqual(m.SalaryPayment.objects.filter(employee=self.emp).count(), 1)
        count_pay = m.SalaryPayment.objects.get(gang_leader=leader)
        self.assertEqual((count_pay.employee, count_pay.basic_amount), (None, D(5000)))
        self.client.post("/m/payroll/generate/", {**post, "confirm": "1"})  # again: nothing doubled
        self.assertEqual(m.SalaryPayment.objects.count(), 2)
        self.assertContains(self.client.get(f"/m/payroll/{count_pay.pk}/payslip/"), "Daily labour count")
        self.assertEqual(self.client.get(f"/m/payroll/{count_pay.pk}/payslip/pdf/").status_code, 200)

    def test_add_new_from_a_dropdown(self):
        form = self.client.get("/m/group-work/add/").content.decode()
        self.assertIn('"add_new"', form)
        self.assertIn("/m/gang-leaders/add/?popup=1", form)
        popup = self.client.get("/m/gang-leaders/add/?popup=1").content.decode()
        self.assertNotIn('id="sidebar"', popup)  # just the form, inside the dialog
        resp = self.client.post("/m/gang-leaders/add/?popup=1", {"name": "Kannan", "is_active": "on"})
        self.assertContains(resp, '"type": "pms-added"')
        leader = m.GangLeader.objects.get(name="Kannan")
        self.assertContains(resp, f'"id": "{leader.pk}"')
        # a writer can't add to staff-only lists, so no "Add new" for them
        self.client.force_login(get_user_model().objects.create_user("writer2", password="pw"))
        form = self.client.get("/m/attendance/add/").content.decode()
        self.assertNotIn("/m/attendance-statuses/add/?popup=1", form)
        self.assertIn("/m/employees/add/?popup=1", form)

    def test_payroll_overwrite_keeps_typed_amounts(self):
        d1, d2 = dt.date(2026, 8, 1), dt.date(2026, 8, 31)
        m.Attendance.objects.create(
            employee=self.emp, date=dt.date(2026, 8, 3), status=lookup(m.AttendanceStatus, pay_fraction=1)
        )
        post = {"date_from": d1, "date_to": d2, "confirm": "1", "include_employees": "on"}
        self.client.post("/m/payroll/generate/", post)
        p = m.SalaryPayment.objects.get(employee=self.emp)
        p.bonus = D(250)
        p.save()
        m.Attendance.objects.create(
            employee=self.emp, date=dt.date(2026, 8, 4), status=lookup(m.AttendanceStatus, pay_fraction=1)
        )
        self.client.post("/m/payroll/generate/", {**post, "overwrite": "on"})
        p2 = m.SalaryPayment.objects.get(employee=self.emp)
        self.assertEqual((p2.pk, p2.bonus, p2.basic_amount), (p.pk, D(250), D(1000)))


class LanguageAndLabelTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("admin", "admin@example.com", "pw"))

    def test_switch_language(self):
        self.client.post("/settings/language/", {"language": "ml", "next": "/"})
        html = self.client.get("/").content.decode()
        self.assertIn('lang="ml"', html)
        self.assertNotIn(">Dashboard<", html)
        self.client.post("/settings/language/", {"language": "en", "next": "https://evil.example/"})
        self.assertIn(">Dashboard<", self.client.get("/").content.decode())

    def test_rename_a_field(self):
        self.client.get("/m/employees/")
        label = Label.objects.get(key="field:estate.employee.full_name")
        self.assertTrue(label.ml)  # translation filled from the translation memory
        label.en = "Worker name"
        label.save()
        self.assertIn("Worker name", self.client.get("/m/employees/").content.decode())


class DemoIsolationTests(TestCase):
    databases = {DEFAULT_DB, DEMO_DB}

    def test_demo_data_never_touches_real_data(self):
        from .demo_data import generate

        user = get_user_model().objects.create_superuser("admin", "admin@example.com", "pw")
        job = m.JobType.objects.create(name="Real job", default_daily_wage=D(500))
        m.Employee.objects.create(full_name="Real worker", job_type=job, employment_type=lookup(m.EmploymentType))
        with use_db(DEMO_DB):
            generate(months=2)
            self.assertGreater(m.Employee.objects.count(), 10)
            self.assertFalse(m.Employee.objects.filter(full_name="Real worker").exists())
        self.assertEqual(list(m.Employee.objects.values_list("full_name", flat=True)), ["Real worker"])
        self.assertFalse(m.Harvest.objects.exists())

        self.client.force_login(user)
        session = self.client.session
        session["pms_demo"] = True
        session.save()
        self.assertNotIn("Real worker", self.client.get("/m/employees/").content.decode())
        # a streamed CSV is read after the view returned: it must still come from the demo data
        csv = b"".join(self.client.get("/m/employees/export/").streaming_content).decode("utf-8-sig")
        self.assertNotIn("Real worker", csv)
        self.assertGreater(csv.count("\n"), 10)
        session["pms_demo"] = False
        session.save()
        self.assertIn("Real worker", self.client.get("/m/employees/").content.decode())

    def test_dashboard_with_demo_data(self):
        from .demo_data import generate

        user = get_user_model().objects.create_superuser("admin", "admin@example.com", "pw")
        with use_db(DEMO_DB):
            generate(months=3)
        self.client.force_login(user)
        session = self.client.session
        session["pms_demo"] = True
        session.save()
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        ctx = r.context
        self.assertGreater(ctx["active_employees"], 0)
        self.assertTrue(ctx["recent_harvests"])
        self.assertEqual(len(ctx["charts"]["money"]["series"]), 2)
        self.assertEqual(len(ctx["charts"]["mandays"]["labels"]), 14)
        html = r.content.decode()
        for part in ("moneyChart", "mandaysChart", "/reports/analysis/?range=this_fy#costs", "/reports/builder/"):
            self.assertIn(part, html)
        # empty database: the dashboard still opens, with friendly empty messages
        session["pms_demo"] = False
        session.save()
        html = self.client.get("/").content.decode()
        self.assertIn("All salaries are paid.", html)


class ChemicalMigrationTests(TransactionTestCase):
    serialized_rollback = True  # the lists seeded by the migrations are back for the other tests

    def test_old_single_chemical_entries_are_moved_to_lines(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        executor.migrate([("estate", "0022_chemical_purchases_and_lines")])
        old = executor.loader.project_state([("estate", "0022_chemical_purchases_and_lines")]).apps
        A, F, T = (old.get_model("estate", n) for n in ("AgriInput", "FieldOperation", "OperationType"))
        a = A.objects.create(name="Old fungicide", unit_cost=D(120))
        op = F.objects.create(
            date=dt.date(2026, 2, 1),
            operation_type=T.objects.first(),
            agri_input=a,
            quantity=D(3),
            unit_cost=D(110),
            input_cost=D(330),
            total_cost=D(330),
        )
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())
        line = m.FieldOperationInput.objects.get(operation_id=op.pk)
        self.assertEqual((line.quantity, line.unit_cost, line.cost), (D(3), D(110), D(330)))
        self.assertEqual(line.purchase.supplier, "Opening stock")
        self.assertEqual(m.AgriInput.objects.get(pk=a.pk).stock_qty, D(0))
