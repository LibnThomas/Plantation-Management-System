"""
Packs the Django project (the same code that runs on the server) into the phone app:
    src/pms_mobile/resources/django_app.zip  +  build_id.txt
Run by build_android.sh / build_ios.sh before every build. Secrets (.env), databases, tests and the old
Android APK are never packed.
"""

import hashlib
import io
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # the project (manage.py is here)
OUT = Path(__file__).resolve().parent / "src" / "pms_mobile" / "resources"
INCLUDE = ["config", "core", "estate", "reports", "templates", "static", "manage.py"]
SKIP_DIRS = {"__pycache__", "staticfiles", "app"}  # static/app holds the old server-download APK
SKIP_NAMES = {".env", ".DS_Store", "tests.py"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".sqlite3", ".apk", ".log"}


def files():
    for name in INCLUDE:
        path = ROOT / name
        if path.is_file():
            yield path
            continue
        for f in sorted(path.rglob("*")):
            rel = f.relative_to(ROOT)
            if f.is_dir() or set(rel.parts[:-1]) & SKIP_DIRS:
                continue
            if f.name in SKIP_NAMES or f.suffix in SKIP_SUFFIXES or f.name.startswith(".env"):
                continue
            yield f


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    buf, digest, count = io.BytesIO(), hashlib.sha256(), 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in files():
            rel = f.relative_to(ROOT).as_posix()
            data = f.read_bytes()
            digest.update(rel.encode() + b"\0" + data)
            info = zipfile.ZipInfo(rel, date_time=(2020, 1, 1, 0, 0, 0))  # same code = same zip
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
            count += 1
    (OUT / "django_app.zip").write_bytes(buf.getvalue())
    build_id = digest.hexdigest()[:16]
    (OUT / "build_id.txt").write_text(build_id + "\n")
    print(f"Packed {count} files ({len(buf.getvalue()) / 1e6:.1f} MB), build {build_id}")


if __name__ == "__main__":
    main()
