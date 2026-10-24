import datetime as dt
import io

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from . import licence
from .models import ActivityLog, AppSettings

TEST_KEY_PEM = None


def _vendor_key():
    """A throw-away key pair for tests, registered as an extra vendor public key."""
    global TEST_KEY_PEM
    if TEST_KEY_PEM is None:
        import base64

        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        key = Ed25519PrivateKey.generate()
        TEST_KEY_PEM = key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
        pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        licence.VENDOR_PUBLIC_KEYS.append(base64.b64encode(pub).decode())
    return TEST_KEY_PEM


class Base(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser("admin", "admin@example.com", "pw")
        self.writer = User.objects.create_user("writer", password="pw")
        self.app = AppSettings.load()
        self.today = timezone.localdate()

    def set_app(self, **kw):
        AppSettings.objects.filter(pk=1).update(**kw)


class SubscriptionTests(Base):
    def test_trial_then_grace_then_read_only(self):
        trial = settings.LICENCE_TRIAL_DAYS
        for days_ago, state in ((0, "trial"), (trial - 3, "expiring"), (trial + 2, "grace"), (trial + 30, "expired")):
            self.set_app(first_run_date=self.today - dt.timedelta(days=days_ago), last_seen_date=self.today)
            self.assertEqual(licence.status(AppSettings.load()).state, state, days_ago)

    def test_setting_the_clock_back_does_not_help(self):
        self.set_app(
            first_run_date=self.today - dt.timedelta(days=100), last_seen_date=self.today + dt.timedelta(days=5)
        )
        self.assertEqual(licence.today_for(AppSettings.load()), self.today + dt.timedelta(days=5))

    def test_read_only_blocks_saving_but_not_viewing(self):
        self.set_app(first_run_date=self.today - dt.timedelta(days=200), last_seen_date=self.today)
        self.client.force_login(self.writer)
        self.assertEqual(self.client.get("/m/harvests/").status_code, 200)
        self.assertContains(self.client.get("/m/employees/export/"), "")
        resp = self.client.post(
            "/m/blocks/add/", {"name": "New block", "area_acres": "1", "plant_count": "0"}, follow=True
        )
        self.assertContains(resp, "read-only")
        from estate.models import Block

        self.assertFalse(Block.objects.exists())

    def test_licence_key_renews_access(self):
        pem = _vendor_key()
        self.set_app(first_run_date=self.today - dt.timedelta(days=200), last_seen_date=self.today)
        self.client.force_login(self.admin)
        self.client.get("/")  # first request creates the installation ID
        app = AppSettings.load()
        key = licence.issue(pem, "Test estate", self.today + dt.timedelta(days=365), install_id=app.install_id)
        self.client.post("/settings/app/", {"tab": "subscription", "action": "licence", "licence_key": key})
        status = licence.status(AppSettings.load())
        self.assertEqual((status.state, status.valid_until), ("active", self.today + dt.timedelta(days=365)))

    def test_key_of_another_installation_or_tampered_is_refused(self):
        pem = _vendor_key()
        key = licence.issue(pem, "X", self.today + dt.timedelta(days=30), install_id="AAAA-BBBB-CCCC")
        with self.assertRaises(licence.LicenceError):
            licence.decode(key, "1111-2222-3333")
        head, body, sig = key.split(".")
        with self.assertRaises(licence.LicenceError):
            licence.decode(f"{head}.{body[:-2]}xx.{sig}", None)

    def test_user_limit(self):
        get_user_model().objects.create_user("second", password="pw")
        get_user_model().objects.create_user("third", password="pw")
        self.set_app(sub_valid_until=self.today + dt.timedelta(days=30), sub_max_users=2)
        self.assertTrue(self.client.login(username="second", password="pw"))
        self.client.logout()
        resp = self.client.post("/login/", {"username": "third", "password": "pw"})
        self.assertContains(resp, "allows 2 users")
        self.assertTrue(self.client.login(username="admin", password="pw"))  # administrators always


class AppSettingsPageTests(Base):
    def test_only_administrators(self):
        self.client.force_login(self.writer)
        self.assertEqual(self.client.get("/settings/app/").status_code, 302)
        self.assertEqual(self.client.get("/settings/app/backup/").status_code, 302)
        self.client.force_login(self.admin)
        for tab in ("general", "theme", "subscription", "security", "activity"):
            self.assertEqual(self.client.get(f"/settings/app/?tab={tab}").status_code, 200, tab)
        backup = self.client.get("/settings/app/backup/")
        data = json.loads(b"".join(backup.streaming_content))
        self.assertTrue(any(row["model"] == "core.appsettings" for row in data))

    def test_theme_is_applied_and_unreadable_colours_refused(self):
        self.client.force_login(self.admin)
        base = {"tab": "theme", "header_color": "#163c69", "background_color": "#f3f6fa", "base_font_size": 16}
        self.client.post("/settings/app/", {**base, "primary_color": "#ffff00"})
        self.assertNotEqual(AppSettings.load().primary_color, "#ffff00")
        self.client.post("/settings/app/", {**base, "primary_color": "#1d4e89"})
        self.assertContains(self.client.get("/"), "--pms-green:#1d4e89")

    def test_profile_logo_and_text_size(self):
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (40, 20), "green").save(buf, "PNG")
        buf.name = "logo.png"
        buf.seek(0)
        self.client.force_login(self.admin)
        self.client.post(
            "/settings/app/",
            {
                "tab": "general",
                "estate_name": "Green Valley",
                "currency_symbol": "₹",
                "financial_year_start_month": 4,
                "crop_season_start_month": 8,
                "employee_code_prefix": "GV",
                "batch_no_prefix": "PB",
                "work_hours_per_day": "8",
                "logo_file": buf,
            },
        )
        app = AppSettings.load()
        self.assertEqual((app.estate_name, app.logo_type), ("Green Valley", "image/png"))
        self.assertEqual(self.client.get("/settings/logo/")["Content-Type"], "image/png")
        self.assertEqual(self.client.get("/m/employees/pdf/").status_code, 200)  # PDF header with the logo
        self.client.post("/settings/text-size/", {"size": 130})
        self.assertContains(self.client.get("/"), "--pms-size:19.5px")

    def test_activity_log_records_real_changes(self):
        self.client.force_login(self.writer)
        self.client.post("/m/blocks/add/", {"name": "Block Z", "area_acres": "1", "plant_count": "0"})
        log = ActivityLog.objects.filter(model__icontains="block").first()
        self.assertIsNotNone(log)
        self.assertEqual((log.user, log.action), (self.writer, "add"))


# ---------------------------------------------------------------------------
# Valid till date: typed by the administrator or fetched from an API
# ---------------------------------------------------------------------------
import http.server  # noqa: E402
import json  # noqa: E402
import threading  # noqa: E402

from django.test import override_settings  # noqa: E402

from . import subscription_api  # noqa: E402


class _Api(http.server.BaseHTTPRequestHandler):
    body = b"{}"
    status = 200
    seen = []

    def do_GET(self):  # noqa: N802
        _Api.seen.append((self.path, self.headers.get("Authorization")))
        self.send_response(_Api.status)
        self.end_headers()
        self.wfile.write(_Api.body)

    def log_message(self, *args):
        pass


@override_settings(SUBSCRIPTION_API_SYNC=True)
class SubscriptionApiTests(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.server = http.server.HTTPServer(("127.0.0.1", 0), _Api)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/pms/licence"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        _Api.status, _Api.seen = 200, []
        subscription_api._started["done"] = False
        self.client.force_login(self.admin)

    def answer(self, data):
        _Api.body = (json.dumps(data) if not isinstance(data, str) else data).encode()

    def save(self, **fields):
        data = {
            "tab": "subscription",
            "action": "save",
            "sub_api_field": "valid_until",
            "sub_api_interval_hours": 12,
            "sub_max_users": 0,
            **fields,
        }
        return self.client.post("/settings/app/", data, follow=True)

    def test_manual_date_by_administrator(self):
        self.save(sub_valid_until="2027-01-31", sub_max_users=5)
        app = AppSettings.load()
        self.assertEqual((app.sub_valid_until, app.sub_max_users), (dt.date(2027, 1, 31), 5))
        self.assertEqual(licence.status(app).state, "active")

    def test_api_sets_the_date_and_ignores_typed_values(self):
        self.answer({"valid_until": "2027-06-30", "max_users": 8, "plan": "Gold"})
        resp = self.save(
            sub_api_enabled="on", sub_api_url=self.url, sub_api_token="s3cret", sub_valid_until="2099-12-31"
        )
        app = AppSettings.load()
        self.assertEqual((app.sub_valid_until, app.sub_max_users, app.sub_plan), (dt.date(2027, 6, 30), 8, "Gold"))
        self.assertContains(resp, "30/06/27")
        path, auth = _Api.seen[-1]
        self.assertIn(f"install_id={app.install_id}", path)
        self.assertEqual(auth, "Bearer s3cret")

    def test_checked_when_the_app_starts_then_every_interval(self):
        self.answer({"valid_until": "2027-06-30"})
        self.set_app(sub_api_enabled=True, sub_api_url=self.url)
        self.client.get("/")  # first request after start
        self.assertEqual(len(_Api.seen), 1)
        self.client.get("/")  # within the interval: no new call
        self.assertEqual(len(_Api.seen), 1)
        AppSettings.objects.filter(pk=1).update(sub_api_checked_at=timezone.now() - dt.timedelta(hours=13))
        self.client.get("/")
        self.assertEqual(len(_Api.seen), 2)

    def test_nested_field_and_plain_text(self):
        self.set_app(sub_api_enabled=True, sub_api_url=self.url, sub_api_field="data.valid_until")
        self.answer({"data": {"valid_until": "31-12-2027", "max_users": "3"}})
        self.assertTrue(subscription_api.check(AppSettings.load())[0])
        self.assertEqual(AppSettings.load().sub_valid_until, dt.date(2027, 12, 31))
        self.answer("2028-01-15")
        self.assertTrue(subscription_api.check(AppSettings.load())[0])
        self.assertEqual(AppSettings.load().sub_valid_until, dt.date(2028, 1, 15))

    def test_failure_keeps_the_last_date(self):
        self.set_app(sub_api_enabled=True, sub_api_url=self.url, sub_valid_until=dt.date(2027, 5, 1))
        for status, body in ((500, "{}"), (200, '{"other": 1}'), (200, '{"valid_until": "soon"}')):
            _Api.status = status
            self.answer(body)
            ok, message = subscription_api.check(AppSettings.load())
            self.assertFalse(ok, body)
        app = AppSettings.load()
        self.assertEqual(app.sub_valid_until, dt.date(2027, 5, 1))
        self.assertTrue(app.sub_api_message)
        self.set_app(sub_api_url="http://127.0.0.1:9/nothing")  # unreachable
        self.assertFalse(subscription_api.check(AppSettings.load())[0])
        self.assertEqual(AppSettings.load().sub_valid_until, dt.date(2027, 5, 1))

    def test_only_web_urls(self):
        self.set_app(sub_api_enabled=True, sub_api_url="file:///etc/passwd")
        ok, message = subscription_api.check(AppSettings.load())
        self.assertFalse(ok)
        resp = self.save(sub_api_enabled="on", sub_api_url="")
        self.assertContains(resp, "Enter the API URL")

    def test_switching_the_api_off_allows_typing_again(self):
        self.answer({"valid_until": "2027-06-30"})
        self.save(sub_api_enabled="on", sub_api_url=self.url)
        self.save(sub_api_url=self.url, sub_valid_until="2027-09-30")
        app = AppSettings.load()
        self.assertEqual((app.sub_api_enabled, app.sub_valid_until), (False, dt.date(2027, 9, 30)))

    def test_licence_key_fills_the_date(self):
        pem = _vendor_key()
        self.client.get("/")
        app = AppSettings.load()
        key = licence.issue(pem, "X", dt.date(2027, 8, 31), install_id=app.install_id, max_users=4)
        self.client.post("/settings/app/", {"tab": "subscription", "action": "licence", "licence_key": key})
        app = AppSettings.load()
        self.assertEqual((app.sub_valid_until, app.sub_max_users), (dt.date(2027, 8, 31), 4))

    @override_settings(SUBSCRIPTION_MANUAL_ALLOWED=False)
    def test_vendor_locked_installation(self):
        self.answer({"valid_until": "2027-02-28"})
        with override_settings(SUBSCRIPTION_API_URL=self.url):
            self.save(sub_api_url="http://example.invalid/", sub_valid_until="2099-12-31")
            app = AppSettings.load()
            self.assertNotEqual(app.sub_valid_until, dt.date(2099, 12, 31))  # typing is ignored
            self.client.get("/")  # the API is always on and uses the server's URL
            self.assertEqual(AppSettings.load().sub_valid_until, dt.date(2027, 2, 28))
