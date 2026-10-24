import datetime as dt
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from core.dbrouter import DEFAULT_DB, DEMO_DB, use_db
from core.models import Label

from . import models as m
from . import payroll


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

    def test_field_operation_cost_from_input_list(self):
        inp = m.AgriInput.objects.create(name="Test NPK", unit=lookup(m.Unit), unit_cost=D(95))
        f = m.FieldOperation.objects.create(operation_type=lookup(m.OperationType), agri_input=inp, quantity=D(10))
        self.assertEqual((f.unit_cost, f.input_cost, f.total_cost), (D(95), D(950), D(950)))
        f2 = m.FieldOperation.objects.create(
            operation_type=lookup(m.OperationType),
            agri_input=inp,
            quantity=D(10),
            unit_cost=D(90),
            workers_count=2,
            wage_per_worker=D(500),
        )
        self.assertEqual((f2.input_cost, f2.labour_cost, f2.total_cost), (D(900), D(1000), D(1900)))

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
        self.assertEqual(self.job.wages_on(day, self.other_type.pk), (D(700), D(99)))  # OT 0 -> job default
        self.assertEqual(self.job.wages_on(day, self.permanent.pk), (D(600), D(70)))
        book = m.WageBook(day, day)
        self.assertEqual(book.wages_on(self.job.pk, day, self.other_type.pk), (D(700), D(99)))

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
        base = {"date": "01/10/26", "product": crop.pk, "quantity": "100", "rate": "1000", "buyer": "Trader", "remarks": "No auction"}
        resp = self.client.post(
            "/m/sales/add/", {**base, "received_now": "30000", "received_date": "01/10/26"}
        )
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
        self.assertEqual((sale.balance, sale.receipt_state, sale.last_received_on), (D(0), "received", dt.date(2026, 10, 9)))
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
