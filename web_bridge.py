"""Talk to the NEO PHOTO website through a hidden browser (QtWebEngine).

The hosting's anti-bot check (Imunify360 "One moment, please...") only lets
real browsers through. So instead of plain HTTP requests the booth keeps one
invisible browser page open on <site>/booth/bridge (a Laravel page). That page
passes the check like any visitor, and every call runs fetch() inside it, with
the page's cookies and CSRF token. Cookies are stored on disk, so a pass
survives restarts.

All of this runs on the Qt main thread; results come back via callbacks.
"""

import itertools
import json
import logging
import time

from PyQt5.QtCore import QObject, QTimer, QUrl

LOG = logging.getLogger("web_bridge")

try:
    from PyQt5.QtCore import Qt
    from PyQt5.QtWebEngineWidgets import QWebEnginePage, QWebEngineProfile, QWebEngineView
    AVAILABLE = True
except Exception as _e:          # PyQtWebEngine not installed / not bundled
    AVAILABLE = False
    _IMPORT_ERROR = _e


class WebBridge(QObject):
    READY_TIMEOUT_S = 45      # page load + anti-bot check
    CALL_TIMEOUT_S = 25

    def __init__(self, bridge_url, api_key, storage_dir, parent=None):
        super().__init__(parent)
        if not AVAILABLE:
            raise RuntimeError(f"PyQtWebEngine unavailable: {_IMPORT_ERROR}")
        self.bridge_url = bridge_url
        self.api_key = api_key
        self._ids = itertools.count(1)
        self._queue = []          # [(path, payload, callback)]
        self._busy = False
        self._ready = False
        self._loading = False
        self._ready_waiters = []
        self.profile = QWebEngineProfile("neobooth", self)
        self.profile.setPersistentStoragePath(str(storage_dir))
        self.profile.setCachePath(str(storage_dir / "cache"))
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.ForcePersistentCookies)
        self.page = QWebEnginePage(self.profile, self)
        self.page.loadFinished.connect(lambda ok: LOG.info(f"[WEB] bridge page loaded ok={ok}"))
        # The anti-bot script rejects a page without a window (outerWidth/Height = 0,
        # "zeroOuterDimensions"). Give it a real 1280x800 window that is never drawn.
        self.view = QWebEngineView()
        self.view.setPage(self.page)
        self.view.setAttribute(Qt.WA_DontShowOnScreen, True)
        self.view.resize(1280, 800)
        self.view.show()

    # ---- public -------------------------------------------------------------
    def call(self, path, payload, callback):
        """POST payload (dict) to path (e.g. "/booth/promo") from inside the
        bridge page. callback(status:int, data:dict|None, error:str|None)."""
        self._queue.append((path, payload, callback, False))
        self._pump()

    # ---- internals ----------------------------------------------------------
    def _pump(self):
        if self._busy or not self._queue:
            return
        self._busy = True
        path, payload, cb, retried = self._queue.pop(0)
        self._when_ready(lambda ok, why: self._run(path, payload, cb, retried) if ok
                         else self._finish(cb, 0, None, why))

    def _finish(self, cb, status, data, err):
        self._busy = False
        try:
            cb(status, data, err)
        except Exception as e:
            LOG.error(f"[WEB] callback failed: {e}")
        QTimer.singleShot(0, self._pump)

    def _when_ready(self, fn):
        if self._ready:
            fn(True, None)
            return
        self._ready_waiters.append(fn)
        if self._loading:
            return
        self._loading = True
        LOG.info(f"[WEB] opening {self.bridge_url}")
        self.page.load(QUrl(self.bridge_url))
        deadline = time.monotonic() + self.READY_TIMEOUT_S

        def check():
            self.page.runJavaScript(
                "typeof window.neoCall === 'function' ? 'ready' : document.title",
                lambda res: on_result(res))

        def on_result(res):
            if res == "ready":
                done(True, None)
            elif time.monotonic() > deadline:
                done(False, f"bridge page not ready (title: {res!r})")
            else:
                QTimer.singleShot(500, check)

        def done(ok, why):
            self._loading = False
            self._ready = ok
            if not ok:
                LOG.warning(f"[WEB] {why}")
                self.page.load(QUrl("about:blank"))     # stop a challenge page reloading forever
            waiters, self._ready_waiters = self._ready_waiters, []
            for w in waiters:
                w(ok, why)

        QTimer.singleShot(800, check)

    def _run(self, path, payload, cb, retried):
        cid = f"c{next(self._ids)}"
        js = "window.neoCall(%s, %s, %s, %s);" % (
            json.dumps(cid), json.dumps(path), json.dumps(payload), json.dumps(self.api_key))
        self.page.runJavaScript(js)
        deadline = time.monotonic() + self.CALL_TIMEOUT_S

        def poll():
            self.page.runJavaScript(
                "(function(){var r=window.__neo&&window.__neo[%s];"
                "if(r){delete window.__neo[%s];return JSON.stringify(r);}"
                "return typeof window.neoCall==='function'?'':'gone';})()" % (json.dumps(cid), json.dumps(cid)),
                got)

        def got(res):
            if res in ("gone", None) or (res == "" and time.monotonic() > deadline):
                # Page navigated away (anti-bot reload, session expiry) or hung: reopen once.
                self._ready = False
                if not retried:
                    self._busy = False
                    self._queue.insert(0, (path, payload, cb, True))
                    QTimer.singleShot(0, self._pump)
                else:
                    self._finish(cb, 0, None, "bridge page lost / timed out")
                return
            if res == "":
                QTimer.singleShot(200, poll)
                return
            try:
                r = json.loads(res)
            except ValueError:
                self._finish(cb, 0, None, f"bad bridge result: {res[:100]}")
                return
            status = int(r.get("status") or 0)
            if status == 419 and not retried:       # CSRF / session expired: reload, retry
                self._ready = False
                self._busy = False
                self._queue.insert(0, (path, payload, cb, True))
                QTimer.singleShot(0, self._pump)
                return
            self._finish(cb, status, r.get("data"), r.get("error") or r.get("text"))

        QTimer.singleShot(150, poll)
