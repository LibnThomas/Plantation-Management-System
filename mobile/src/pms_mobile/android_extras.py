"""
Android only (imported on Android): what a browser does but a bare web view doesn't.

- Downloads: PDF, Excel (CSV) and backup files are saved to Downloads/PlantationMS and opened with a
  suitable app (PDF viewer, Sheets ...). The file is fetched from the app's own server inside the phone,
  with the logged-in session.
- Back button: goes back a page; on the first page it leaves the app as usual.
"""

import threading
import traceback
import urllib.request

from android.content import ContentValues, Intent
from android.os import Build, Environment
from android.provider import MediaStore
from android.view import KeyEvent, View
from android.webkit import CookieManager, DownloadListener, URLUtil
from android.widget import Toast
from java import dynamic_proxy
from java.io import File, FileOutputStream
from java.lang import Runnable

FOLDER = "PlantationMS"


class _OnUi(dynamic_proxy(Runnable)):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            self.fn()
        except Exception:
            traceback.print_exc()


class _Downloads(dynamic_proxy(DownloadListener)):
    def __init__(self, activity):
        super().__init__()
        self.activity = activity

    def onDownloadStart(self, url, user_agent, content_disposition, mimetype, length):
        threading.Thread(
            target=self._save, args=(url, content_disposition, mimetype), daemon=True, name="pms-download"
        ).start()

    def _toast(self, text):
        self.activity.runOnUiThread(_OnUi(lambda: Toast.makeText(self.activity, text, Toast.LENGTH_LONG).show()))

    def _save(self, url, content_disposition, mimetype):
        try:
            name = URLUtil.guessFileName(url, content_disposition, mimetype)
            request = urllib.request.Request(url, headers={"Cookie": CookieManager.getInstance().getCookie(url) or ""})
            with urllib.request.urlopen(request, timeout=300) as resp:
                data = resp.read()
                mimetype = resp.headers.get_content_type() or mimetype
            uri = self._write(name, mimetype, data)
            self._toast(f"Saved to Downloads/{FOLDER}: {name}")
            if uri is not None:
                self.activity.runOnUiThread(_OnUi(lambda: self._open(uri, mimetype)))
        except Exception as e:
            traceback.print_exc()
            self._toast(f"Could not save the file: {e}")

    def _write(self, name, mimetype, data):
        if Build.VERSION.SDK_INT >= 29:  # Android 10+: Downloads, no permission needed
            values = ContentValues()
            values.put(MediaStore.MediaColumns.DISPLAY_NAME, name)
            values.put(MediaStore.MediaColumns.MIME_TYPE, mimetype)
            values.put(MediaStore.MediaColumns.RELATIVE_PATH, f"{Environment.DIRECTORY_DOWNLOADS}/{FOLDER}")
            resolver = self.activity.getContentResolver()
            uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
            out = resolver.openOutputStream(uri)
            try:
                out.write(data)
            finally:
                out.close()
            return uri
        # Android 7 - 9: the app's own folder in the phone storage (Android/data/<app>/files/Download)
        folder = File(self.activity.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS), FOLDER)
        folder.mkdirs()
        out = FileOutputStream(File(folder, name))
        try:
            out.write(data)
        finally:
            out.close()
        return None

    def _open(self, uri, mimetype):
        intent = Intent(Intent.ACTION_VIEW)
        intent.setDataAndType(uri, mimetype)
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK)
        try:
            self.activity.startActivity(Intent.createChooser(intent, "Open with"))
        except Exception:  # no app for this kind of file: it is still in Downloads
            traceback.print_exc()


class _Back(dynamic_proxy(View.OnKeyListener)):
    def onKey(self, view, key_code, event):
        if key_code == KeyEvent.KEYCODE_BACK and view.canGoBack():
            if event.getAction() == KeyEvent.ACTION_UP:
                view.goBack()
            return True
        return False


def install(app, webview):
    native = webview._impl.native  # android.webkit.WebView
    activity = app._impl.native  # org.beeware.android.MainActivity
    app._pms_downloads = _Downloads(activity)  # keep references: Java only holds weak ones to proxies
    app._pms_back = _Back()
    native.setDownloadListener(app._pms_downloads)
    native.setOnKeyListener(app._pms_back)
    settings = native.getSettings()
    settings.setTextZoom(100)  # the app has its own text size setting
    native.requestFocus()
