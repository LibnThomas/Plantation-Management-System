"""
The phone app's screen: one full-screen web view showing the app, which runs inside the phone
(see launcher.py). Android extras (android_extras.py): files (PDF, Excel/CSV, backup) are saved to
Downloads/PlantationMS, and the Back button goes back a page.
"""

import asyncio
import html
import traceback

import toga

from . import launcher

LOADING = """<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
font-family:system-ui,sans-serif;background:#174a31;color:#fff;text-align:center}}
.s{{width:42px;height:42px;border:4px solid rgba(255,255,255,.3);border-top-color:#fff;border-radius:50%;
animation:r 1s linear infinite;margin:0 auto 18px}}@keyframes r{{to{{transform:rotate(360deg)}}}}
p{{opacity:.85;font-size:15px;margin:6px 24px}}</style></head><body><div>
<div class="s"></div><h3>Plantation Management System</h3><p>{message}</p>
<p style="font-size:12px">{note}</p></div></body></html>"""

FAILED = """<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="font-family:system-ui,sans-serif;padding:20px"><h3>The app could not start</h3>
<p>Please close the app and open it again. If it happens again, send this text to Mentors Soft Tech:</p>
<pre style="white-space:pre-wrap;font-size:11px;background:#f4f4f4;padding:10px">{error}</pre></body></html>"""


class PlantationApp(toga.App):
    def startup(self):
        self.web = toga.WebView(style=toga.style.Pack(flex=1))
        self.main_window = toga.MainWindow(title=self.formal_name)
        self.main_window.content = self.web
        self._show(LOADING.format(message="Starting…", note=""))
        self.main_window.show()
        self.loop.create_task(self._start())

    def _show(self, page):
        self.web.set_content("http://127.0.0.1/", page)

    def _progress(self, text):
        note = "First start: this takes a minute. Nothing needs the internet."
        self.loop.call_soon_threadsafe(self._show, LOADING.format(message=html.escape(text), note=note))

    async def _start(self):
        platform = toga.platform.current_platform  # android / iOS
        try:
            url = await asyncio.get_running_loop().run_in_executor(
                None, lambda: launcher.start(self.paths.data, platform.lower(), progress=self._progress)
            )
        except Exception:
            self._show(FAILED.format(error=html.escape(traceback.format_exc())))
            return
        try:
            if platform == "android":
                from . import android_extras

                android_extras.install(self, self.web)
            elif platform == "iOS":  # swipe from the edge to go back a page
                self.web._impl.native.allowsBackForwardNavigationGestures = True
        except Exception:  # the app still works; only downloads / Back are missing
            traceback.print_exc()
        self.web.url = url


def main():
    return PlantationApp("Plantation MS", "com.mentorssofttech.pms_mobile")
