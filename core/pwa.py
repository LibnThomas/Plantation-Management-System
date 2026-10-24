"""
Install the app on phones: web app manifest + service worker (Android / iPhone "Add to Home screen")
and the page that explains how to install it, with a QR code to share the link and the Android app.
"""

import io
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.views.decorators.cache import cache_control

from .i18n import ui

APK_PATH = Path(settings.BASE_DIR) / "static" / "app" / "plantation-ms.apk"


def manifest(request):
    app = request.app_settings
    icons = [{"src": static(f"icons/icon-{s}.png"), "sizes": f"{s}x{s}", "type": "image/png"} for s in (192, 512)]
    icons += [
        {"src": static(f"icons/maskable-{s}.png"), "sizes": f"{s}x{s}", "type": "image/png", "purpose": "maskable"}
        for s in (192, 512)
    ]
    data = {
        "id": "/",
        "name": f"{ui('Plantation Management System')} · {app.estate_name}",
        "short_name": "PMS",
        "description": ui("Workers, attendance, wages, field work, harvest, processing and reports"),
        "start_url": "/?source=app",
        "scope": "/",
        "display": "standalone",
        "orientation": "any",
        "background_color": app.background_color,
        "theme_color": app.header_color,
        "icons": icons,
        "lang": request.LANGUAGE_CODE,
    }
    resp = JsonResponse(data, json_dumps_params={"ensure_ascii": False})
    resp["Content-Type"] = "application/manifest+json"
    return resp


@cache_control(no_cache=True)
def service_worker(request):
    resp = render(request, "pwa/sw.js", {"version": settings.PWA_VERSION}, content_type="application/javascript")
    resp["Service-Worker-Allowed"] = "/"
    return resp


def offline(request):
    return render(request, "pwa/offline.html")


def _qr_svg(text):
    try:
        import segno
    except ImportError:
        return ""
    out = io.BytesIO()
    segno.make(text, error="m").save(
        out, kind="svg", scale=5, border=2, dark="#1d2b22", xmldecl=False, svgns=True, nl=False
    )
    return out.getvalue().decode()


@login_required
def install(request):
    url = request.build_absolute_uri("/")
    return render(
        request,
        "pwa/install.html",
        {
            "app_url": url,
            "qr_app": _qr_svg(url),
            "is_https": request.is_secure(),
            "apk_available": APK_PATH.exists(),
            "apk_size_mb": round(APK_PATH.stat().st_size / 1e6, 1) if APK_PATH.exists() else 0,
            "qr_apk": _qr_svg(request.build_absolute_uri("/app/android.apk")) if APK_PATH.exists() else "",
        },
    )


def android_apk(request):
    """The Android app for download / sharing (no Play Store needed)."""
    if not APK_PATH.exists():
        raise Http404
    resp = FileResponse(APK_PATH.open("rb"), content_type="application/vnd.android.package-archive")
    resp["Content-Disposition"] = 'attachment; filename="plantation-ms.apk"'
    return resp


def favicon(request):
    path = Path(settings.BASE_DIR) / "static" / "icons" / "favicon-32.png"
    return HttpResponse(path.read_bytes(), content_type="image/png")
