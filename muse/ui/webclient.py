"""Trình duyệt tích hợp (QtWebEngine) mở đúng trang tempmail.plus.

* Các yêu cầu API của lô được gửi bằng ``fetch`` từ bên trong trang, nên dùng chung cookie
  và cách gửi giống hệt khi bạn thao tác trên web.
* Khi trang đòi captcha, ô reCAPTCHA của chính trang được hiện ra để NGƯỜI DÙNG tự tích.
  Ứng dụng không tự giải captcha; nó chỉ chờ mã xác minh rồi gửi kèm như trang web làm.

``WebSession`` có giao diện giống ``requests.Session`` (``request`` + ``headers``) để
``TempMailClient`` dùng được mà không cần sửa.
"""

from __future__ import annotations

import itertools
import json
import threading
from urllib.parse import urlsplit

import requests
from PySide6.QtCore import QObject, QTimer, QUrl, Signal, Slot

from muse.core.api import BASE_URL
from muse.core.storage import app_home

try:
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
    from PySide6.QtWebEngineWidgets import QWebEngineView
    WEB_AVAILABLE = True
except Exception:  # noqa: BLE001 – máy không có QtWebEngine: dùng requests như trước
    WEB_AVAILABLE = False

# site key reCAPTCHA của tempmail.plus (lấy từ lib/main.js của trang)
SITE_KEY = "6LeeyKgUAAAAAKSlugFleu1vNMVItM3UHSTbXoTD"
START_URL = BASE_URL + "/en/"

_FETCH_JS = """
(function(){
  window.__muse = window.__muse || {};
  var id = %(id)d, m = %(method)s, url = %(url)s, p = new URLSearchParams(%(params)s);
  var o = {method: m, credentials: 'same-origin', headers: {'X-Requested-With': 'XMLHttpRequest'}};
  if (m === 'GET') { if (p.toString()) url += '?' + p.toString(); }
  else { o.body = p; o.headers['Content-Type'] = 'application/x-www-form-urlencoded; charset=UTF-8'; }
  fetch(url, o).then(function(r){ return r.text().then(function(t){ window.__muse[id] = {s: r.status, t: t}; }); })
    .catch(function(e){ window.__muse[id] = {e: String(e)}; });
})();
"""

_CAPTCHA_JS = """
(function(){
  window.__museTok = null;
  var old = document.getElementById('muse-cap'); if (old) old.remove();
  var d = document.createElement('div'); d.id = 'muse-cap';
  d.style.cssText = 'position:fixed;left:0;top:0;right:0;bottom:0;z-index:2147483647;background:#fff;' +
    'display:flex;flex-direction:column;align-items:center;padding-top:18px;' +
    'font:15px Segoe UI,Arial,sans-serif;color:#12355b;overflow:auto';
  d.innerHTML = '<p style="margin:0 16px 14px;text-align:center">TempMail.Plus cần xác minh. ' +
    'Hãy tích vào ô <b>Tôi không phải là người máy</b> (làm theo nếu có hình ảnh).</p>' +
    '<div id="muse-cap-w"></div>';
  document.body.appendChild(d);
  var tries = 0;
  function go(){
    if (!(window.grecaptcha && grecaptcha.render)) {
      if (++tries === 20) {
        var s = document.createElement('script');
        s.src = 'https://www.google.com/recaptcha/api.js?render=explicit&hl=vi';
        document.head.appendChild(s);
      }
      return setTimeout(go, 300);
    }
    try {
      grecaptcha.render('muse-cap-w', {sitekey: %(key)s,
        callback: function(t){ window.__museTok = t; },
        'expired-callback': function(){ window.__museTok = null; }});
    } catch (e) { setTimeout(go, 500); }
  }
  go();
})();
"""


_PROFILE = None


def shared_profile():
    """Một profile dùng chung cho cả ứng dụng (giữ cookie giữa các lần tạo lô).

    Gắn vào QApplication để profile luôn sống lâu hơn mọi trang web dùng nó."""
    global _PROFILE
    if _PROFILE is None:
        from PySide6.QtWidgets import QApplication
        root = app_home() / "web"
        _PROFILE = QWebEngineProfile("MuseMailbox", QApplication.instance())
        _PROFILE.setPersistentStoragePath(str(root / "storage"))
        _PROFILE.setCachePath(str(root / "cache"))
    return _PROFILE


class _Resp:
    def __init__(self, status: int, text: str):
        self.status_code = status
        self.text = text

    def json(self):
        return json.loads(self.text)


class WebBridge(QObject):
    """Sống ở luồng giao diện; luồng nền gọi ``fetch()``/``solve_captcha()`` và chờ kết quả."""

    captcha_shown = Signal()
    captcha_hidden = Signal()
    _start_fetch = Signal(int, str)
    _start_captcha = Signal()
    _cancel_captcha = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ready = threading.Event()
        self.load_ok = False
        self._ids = itertools.count(1)
        self._pending: dict[int, tuple[threading.Event, dict]] = {}
        self._cap_event: threading.Event | None = None
        self._cap_result: dict = {}

        self.profile = shared_profile()
        self.page = QWebEnginePage(self.profile, self)
        self.view = QWebEngineView()
        self.view.setPage(self.page)
        self.view.setMinimumSize(380, 520)
        self.page.loadFinished.connect(self._on_loaded)

        self._poll = QTimer(self)
        self._poll.setInterval(150)
        self._poll.timeout.connect(self._tick)
        self._start_fetch.connect(self._do_fetch)
        self._start_captcha.connect(self._do_captcha)
        self._cancel_captcha.connect(self._do_cancel_captcha)
        self.page.load(QUrl(START_URL))

    # ------------------------------------------------------------ GUI thread
    @Slot(bool)
    def _on_loaded(self, ok: bool) -> None:
        self.load_ok = ok and urlsplit(self.page.url().toString()).netloc.endswith("tempmail.plus")
        self.ready.set()

    @Slot(int, str)
    def _do_fetch(self, rid: int, js: str) -> None:
        self.page.runJavaScript(js, 0)
        self._poll.start()

    @Slot()
    def _do_captcha(self) -> None:
        self.page.runJavaScript(_CAPTCHA_JS % {"key": json.dumps(SITE_KEY)}, 0)
        self.captcha_shown.emit()
        self._poll.start()

    @Slot()
    def _do_cancel_captcha(self) -> None:
        self._finish_captcha(None)

    def cancel_captcha(self) -> None:
        """Gọi từ giao diện (nút Bỏ qua / Dừng)."""
        self._finish_captcha(None)

    def _finish_captcha(self, token: str | None) -> None:
        ev = self._cap_event
        if ev is None:
            return
        self._cap_event = None
        self._cap_result["token"] = token
        self.page.runJavaScript(
            "(function(){var d=document.getElementById('muse-cap'); if(d) d.remove();})();", 0)
        self.captcha_hidden.emit()
        ev.set()

    def _tick(self) -> None:
        if not self._pending and self._cap_event is None:
            self._poll.stop()
            return
        for rid in list(self._pending):
            self.page.runJavaScript(
                f"(function(){{var r=window.__muse&&window.__muse[{rid}];"
                f"if(r) delete window.__muse[{rid}]; return r ? JSON.stringify(r) : null;}})()",
                0, lambda res, rid=rid: self._got(rid, res))
        if self._cap_event is not None:
            self.page.runJavaScript("window.__museTok || null", 0, self._got_token)

    def _got(self, rid: int, res) -> None:
        if not res or rid not in self._pending:
            return
        ev, box = self._pending.pop(rid)
        box.update(json.loads(res))
        ev.set()

    def _got_token(self, token) -> None:
        if token and self._cap_event is not None:
            self._finish_captcha(str(token))

    def dispose(self) -> None:
        self._poll.stop()
        self.cancel_captcha()
        self.page.deleteLater()
        self.view.deleteLater()

    # ------------------------------------------------------------ worker thread
    def fetch(self, method: str, url: str, params: dict, timeout: float) -> _Resp:
        if not self.ready.wait(45):
            raise requests.ConnectionError("Trình duyệt tích hợp chưa tải xong tempmail.plus")
        if not self.load_ok:
            raise requests.ConnectionError("Trình duyệt tích hợp không mở được tempmail.plus")
        rid = next(self._ids)
        ev, box = threading.Event(), {}
        self._pending[rid] = (ev, box)
        js = _FETCH_JS % {
            "id": rid, "method": json.dumps(method.upper()), "url": json.dumps(url),
            "params": json.dumps({k: "" if v is None else str(v) for k, v in params.items()}),
        }
        self._start_fetch.emit(rid, js)
        if not ev.wait(timeout):
            self._pending.pop(rid, None)
            raise requests.Timeout("TempMail.Plus không phản hồi")
        if "e" in box:
            raise requests.ConnectionError(box["e"])
        return _Resp(int(box.get("s", 0)), box.get("t", ""))

    def solve_captcha(self, should_stop=lambda: False) -> str | None:
        """Hiện ô captcha, chờ người dùng tích. Trả về mã xác minh, hoặc None nếu huỷ/dừng."""
        if not self.ready.wait(45) or not self.load_ok:
            return None
        ev = threading.Event()
        self._cap_result = {}
        self._cap_event = ev
        self._start_captcha.emit()
        while not ev.wait(0.3):
            if should_stop():
                self._cancel_captcha.emit()
                ev.wait(5)
                return None
        return self._cap_result.get("token")


class WebSession:
    """Giả lập ``requests.Session`` nhưng gửi qua trình duyệt tích hợp."""

    def __init__(self, bridge: WebBridge):
        self.bridge = bridge
        self.headers: dict[str, str] = {}

    def request(self, method, url, params=None, data=None, timeout=20.0):
        return self.bridge.fetch(method, url, params or data or {}, timeout or 20.0)
