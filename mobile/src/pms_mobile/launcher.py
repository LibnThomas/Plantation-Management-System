"""
Runs the whole Plantation Management System inside the phone, offline.

On every start:
  1. the app's code (bundled as resources/django_app.zip) is unpacked into the app's private data folder
     the first time and again after an update;
  2. the database (SQLite, in the same private folder) is created / brought up to date (migrate). The
     migrations also fill the starting drop-down lists and App Settings;
  3. on the very first start an administrator login is made: admin / admin1234 (shown on the login page
     until it has been used);
  4. a small web server starts on 127.0.0.1 (reachable only from inside the phone) and the app's screen
     shows it.

Nothing needs the internet. The data folder is removed only when the app is uninstalled - use
App Settings > Download backup regularly.

Also usable on a computer to try the phone build:   python -m pms_mobile.launcher --data /tmp/pms-phone
"""

import contextlib
import io
import os
import secrets
import shutil
import socket
import socketserver
import sys
import tempfile
import threading
import zipfile
from importlib import resources
from pathlib import Path
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer

PORTS = range(48765, 48785)  # the same port every time when free: saved column choices stay
_LOCK = threading.Lock()  # one start at a time


def _resource_bytes(name):
    """A file from pms_mobile/resources - a real file on iPhone; inside the APK on Android."""
    here = Path(__file__).resolve().parent / "resources" / name
    if here.exists():
        return here.read_bytes()
    try:
        return resources.files("pms_mobile").joinpath("resources", name).read_bytes()
    except Exception:  # noqa: BLE001 - last way below
        import pkgutil

        data = pkgutil.get_data("pms_mobile", f"resources/{name}")
        if data is None:
            raise FileNotFoundError(name) from None
        return data


def bundled_build_id():
    try:
        return _resource_bytes("build_id.txt").decode().strip()
    except (FileNotFoundError, OSError):
        return "dev"


def unpack_code(data_dir):
    """The app's code, unpacked into <data>/app (again only when the bundled version changed)."""
    data_dir = Path(data_dir)
    code = data_dir / "app"
    build_id = bundled_build_id()
    marker = code / ".build_id"
    if marker.exists() and marker.read_text().strip() == build_id:
        return code
    for left in data_dir.glob("app.new-*"):  # an unpacking cut short (phone switched off ...)
        shutil.rmtree(left, ignore_errors=True)
    fresh = Path(tempfile.mkdtemp(prefix="app.new-", dir=data_dir))
    with zipfile.ZipFile(io.BytesIO(_resource_bytes("django_app.zip"))) as z:
        z.extractall(fresh)
    (fresh / ".build_id").write_text(build_id)
    old = data_dir / "app.old"
    shutil.rmtree(old, ignore_errors=True)
    if code.exists():
        code.rename(old)
    fresh.rename(code)
    shutil.rmtree(old, ignore_errors=True)
    return code


def _secret_key(data_dir):
    path = Path(data_dir) / "secret.key"
    if not path.exists():
        path.write_text(secrets.token_urlsafe(50))
    return path.read_text().strip()


def configure(data_dir, platform="android", code_dir=None):
    """Environment for config/settings.py (the phone never reads a .env file)."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    code = Path(code_dir) if code_dir else unpack_code(data_dir)
    if str(code) not in sys.path:
        sys.path.insert(0, str(code))
    os.environ.update(
        {
            "DJANGO_SETTINGS_MODULE": "config.settings",
            "PMS_MOBILE": "1",
            "PMS_MOBILE_PLATFORM": platform,
            "PMS_DATA_DIR": str(data_dir),
            "DB_ENGINE": "sqlite",
            "SQLITE_PATH": str(data_dir / "pms.sqlite3"),
            "SQLITE_DEMO_PATH": str(data_dir / "pms_demo.sqlite3"),
            "DJANGO_DEBUG": "False",
            "DJANGO_SECRET_KEY": _secret_key(data_dir),
            "DJANGO_ALLOWED_HOSTS": "127.0.0.1,localhost",
            "SECURE_COOKIES": "False",
        }
    )
    return code


def setup_database(data_dir, progress=None):
    """Create / update the database; first start: the administrator login."""
    import django

    say = progress or (lambda text: None)
    django.setup()
    from django.conf import settings
    from django.contrib.auth import get_user_model
    from django.core.management import call_command

    marker = Path(data_dir) / ".migrated"
    build_id = bundled_build_id()
    if not marker.exists() or marker.read_text().strip() != build_id or build_id == "dev":
        say("Preparing the database…")
        call_command("migrate", interactive=False, verbosity=0)
        marker.write_text(build_id)
    users = get_user_model().objects
    if not users.exists():
        say("Creating the administrator login…")
        users.create_superuser(settings.MOBILE_DEFAULT_USER, "", settings.MOBILE_DEFAULT_PASSWORD)


class _Server(socketserver.ThreadingMixIn, WSGIServer):
    daemon_threads = True
    allow_reuse_address = True


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):  # no request log on the phone
        pass


def serve():
    """Start the web server in a background thread; returns its address."""
    from django.core.wsgi import get_wsgi_application

    application = get_wsgi_application()
    last_error = None
    for port in PORTS:
        try:
            httpd = _Server(("127.0.0.1", port), _QuietHandler)
        except OSError as e:  # in use: try the next one
            last_error = e
            continue
        httpd.set_app(application)
        threading.Thread(target=httpd.serve_forever, name="pms-server", daemon=True).start()
        return f"http://127.0.0.1:{port}/"
    raise RuntimeError(f"No free port for the app's server: {last_error}")


def wait_until_up(url, seconds=20):
    host, port = url.split("//")[1].strip("/").split(":")
    for _ in range(seconds * 10):
        try:
            with socket.create_connection((host, int(port)), timeout=0.1):
                return True
        except OSError:
            threading.Event().wait(0.1)
    return False


def start(data_dir, platform="android", code_dir=None, progress=None):
    """Everything above, in order. Returns the address to open in the app's screen."""
    global _URL
    with _LOCK:
        if _URL:  # already running (the screen was re-created)
            return _URL
        configure(data_dir, platform, code_dir)
        setup_database(data_dir, progress)
        url = serve()
        wait_until_up(url)
        _URL = url
        return url


_URL = None


if __name__ == "__main__":  # try the phone build on a computer
    import argparse

    parser = argparse.ArgumentParser(description="Run the phone version of the app on this computer.")
    parser.add_argument("--data", default=str(Path.home() / ".pms-phone"), help="data folder (database etc.)")
    parser.add_argument("--code", default=None, help="use this project folder instead of the bundled zip")
    args = parser.parse_args()
    address = start(args.data, "desktop", args.code, progress=print)
    print("Running:", address, "(Ctrl+C to stop)")
    with contextlib.suppress(KeyboardInterrupt):
        threading.Event().wait()
