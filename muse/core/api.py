"""Client cho các endpoint công khai mà chính trang tempmail.plus sử dụng.

Các endpoint lấy từ mã JavaScript của trang (index.js, lib/main.js):

* ``GET  /api/box``         – đọc cài đặt hộp thư (``ttl_minutes``); lỗi 1021 = sai PIN,
                               1022 = cần captcha.
* ``POST /api/box``         – lưu cài đặt: ``ttl_minutes`` và ``pin``. Có thể trả về
                               ``with_captcha`` khi trang yêu cầu xác minh reCAPTCHA.
* ``GET  /api/box/hidden``  – lấy địa chỉ bí mật của hộp thư.
* ``GET  /api/mails``       – danh sách thư.
* ``GET  /api/mails/<id>``  – nội dung một thư.
* ``DELETE /api/mails/``    – xoá toàn bộ hộp thư.

Mọi yêu cầu đều gửi kèm ``epin`` (PIN hiện tại của hộp thư, rỗng nếu không có).
"""

from __future__ import annotations

import random
import string
from dataclasses import dataclass, field

import requests

BASE_URL = "https://tempmail.plus"

DOMAINS = [
    "mailto.plus",
    "fexpost.com",
    "fexbox.org",
    "mailbox.in.ua",
    "rover.info",
    "fextemp.com",
    "any.pink",
    "merepost.com",
]

# Các mức thời hạn mà trang cho phép (phút) -> nhãn hiển thị
LIFETIMES: dict[int, str] = {
    10: "10 phút",
    60: "60 phút",
    2880: "2 ngày",
    10080: "7 ngày",
}
DEFAULT_TTL = 10080  # 7 ngày

ERR_WRONG_PIN = 1021
ERR_CAPTCHA = 1022


class TempMailError(Exception):
    """Lỗi khi gọi TempMail.Plus."""

    def __init__(self, message: str, code: int | None = None, captcha: bool = False):
        super().__init__(message)
        self.code = code
        self.captcha = captcha


class CaptchaRequired(TempMailError):
    """Trang yêu cầu xác minh reCAPTCHA – không thể tiếp tục tự động."""

    def __init__(self, message: str = "TempMail.Plus yêu cầu xác minh captcha"):
        super().__init__(message, code=ERR_CAPTCHA, captcha=True)


class WrongPin(TempMailError):
    def __init__(self, message: str = "PIN không đúng"):
        super().__init__(message, code=ERR_WRONG_PIN)


@dataclass
class BoxSettings:
    ttl_minutes: int | None
    raw: dict = field(default_factory=dict)


@dataclass
class MailSummary:
    mail_id: int
    sender: str
    sender_name: str
    subject: str
    date: str
    is_new: bool = False


@dataclass
class MailDetail:
    mail_id: int
    sender: str
    sender_name: str
    to: str
    subject: str
    date: str
    text: str
    html: str
    attachments: list = field(default_factory=list)
    raw: dict = field(default_factory=dict)


def random_name(length: int = 10) -> str:
    """Tên ngẫu nhiên giống nút "Tên ngẫu nhiên mới" (chữ thường + số, bắt đầu bằng chữ)."""
    first = random.choice(string.ascii_lowercase)
    rest = "".join(random.choices(string.ascii_lowercase + string.digits, k=length - 1))
    return first + rest


def split_address(address: str) -> tuple[str, str]:
    address = address.strip()
    if address.count("@") != 1:
        raise ValueError("Địa chỉ không hợp lệ")
    name, domain = address.split("@")
    if not name or not domain:
        raise ValueError("Địa chỉ không hợp lệ")
    return name, domain.lower()


def is_valid_address(address: str) -> bool:
    import re

    try:
        name, domain = split_address(address)
    except ValueError:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9]+([.\-_][A-Za-z0-9]+)*", name)) and "." in domain


class TempMailClient:
    def __init__(self, base_url: str = BASE_URL, session: requests.Session | None = None,
                 timeout: float = 20.0):
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.session.headers.setdefault(
            "User-Agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        )
        self.session.headers.setdefault("X-Requested-With", "XMLHttpRequest")
        self.timeout = timeout

    # ------------------------------------------------------------------ low level
    def _call(self, method: str, path: str, data: dict) -> dict:
        url = self.base_url + path
        try:
            if method in ("GET",):
                resp = self.session.request(method, url, params=data, timeout=self.timeout)
            else:
                resp = self.session.request(method, url, data=data, timeout=self.timeout)
        except requests.RequestException as exc:
            raise TempMailError(f"Không kết nối được TempMail.Plus: {exc}") from exc
        if resp.status_code >= 400:
            raise TempMailError(f"TempMail.Plus trả về HTTP {resp.status_code}")
        try:
            j = resp.json()
        except ValueError as exc:
            raise TempMailError("Phản hồi không phải JSON") from exc
        if not isinstance(j, dict):
            raise TempMailError("Phản hồi không hợp lệ")
        if j.get("with_captcha"):
            raise CaptchaRequired()
        err = j.get("err")
        if not j.get("result", True) and err:
            code = err.get("code") if isinstance(err, dict) else None
            msg = (err.get("msg") if isinstance(err, dict) else None) or str(err)
            if code == ERR_CAPTCHA:
                raise CaptchaRequired()
            if code == ERR_WRONG_PIN:
                raise WrongPin()
            raise TempMailError(f"TempMail.Plus báo lỗi: {msg}", code=code)
        return j

    # ------------------------------------------------------------------ box settings
    @staticmethod
    def _with(data: dict, recaptcha: str | None) -> dict:
        if recaptcha:
            data["recaptcha"] = recaptcha  # mã xác minh reCAPTCHA, giống trang web gửi kèm
        return data

    def get_box(self, email: str, epin: str = "", recaptcha: str | None = None) -> BoxSettings:
        j = self._call("GET", "/api/box", self._with({"email": email, "epin": epin}, recaptcha))
        ttl = j.get("ttl_minutes")
        return BoxSettings(ttl_minutes=int(ttl) if ttl is not None else None, raw=j)

    def set_box(self, email: str, ttl_minutes: int, pin: str = "", epin: str = "",
                recaptcha: str | None = None) -> None:
        if ttl_minutes not in LIFETIMES:
            raise ValueError(f"Thời hạn không hợp lệ: {ttl_minutes}")
        j = self._call("POST", "/api/box", self._with(
            {"email": email, "ttl_minutes": ttl_minutes, "pin": pin, "epin": epin}, recaptcha))
        if not j.get("result"):
            raise TempMailError("TempMail.Plus không lưu cài đặt")

    def apply_settings(self, email: str, ttl_minutes: int, pin: str = "",
                       epin: str = "") -> bool:
        """Lưu cài đặt rồi đọc lại để xác nhận. Trả về True khi phía trang đã áp dụng."""
        self.set_box(email, ttl_minutes, pin=pin, epin=epin)
        check = self.get_box(email, epin=pin)
        return check.ttl_minutes == ttl_minutes

    def get_secret_address(self, email: str, epin: str = "",
                           recaptcha: str | None = None) -> str | None:
        j = self._call("GET", "/api/box/hidden",
                       self._with({"email": email, "epin": epin}, recaptcha))
        return j.get("email") or None

    # ------------------------------------------------------------------ mails
    def list_mails(self, email: str, epin: str = "", limit: int = 20) -> list[MailSummary]:
        j = self._call("GET", "/api/mails", {"email": email, "limit": limit, "epin": epin})
        out = []
        for m in j.get("mail_list") or []:
            out.append(MailSummary(
                mail_id=int(m.get("mail_id", 0)),
                sender=m.get("from_mail", "") or "",
                sender_name=m.get("from_name", "") or "",
                subject=m.get("subject", "") or "(không có tiêu đề)",
                date=m.get("time", "") or "",
                is_new=bool(m.get("is_new")),
            ))
        return out

    def get_mail(self, email: str, mail_id: int, epin: str = "") -> MailDetail:
        j = self._call("GET", f"/api/mails/{int(mail_id)}", {"email": email, "epin": epin})
        return MailDetail(
            mail_id=int(mail_id),
            sender=j.get("from_mail", "") or j.get("from", "") or "",
            sender_name=j.get("from_name", "") or "",
            to=_recipient(j),
            subject=j.get("subject", "") or "(không có tiêu đề)",
            date=j.get("date", "") or "",
            text=j.get("text", "") or "",
            html=j.get("html", "") or "",
            attachments=j.get("attachments") or [],
            raw=j,
        )

    def destroy_inbox(self, email: str, first_id: int = 0, epin: str = "") -> None:
        self._call("DELETE", "/api/mails/", {"email": email, "first_id": first_id, "epin": epin})

    def clear_inbox(self, email: str, epin: str = "") -> int:
        """Xoá toàn bộ thư trong hộp (giống nút "Destroy inbox" trên trang).
        Trả về số thư có trước khi xoá; 0 nếu hộp trống."""
        j = self._call("GET", "/api/mails", {"email": email, "limit": 100, "epin": epin})
        mails = j.get("mail_list") or []
        first_id = j.get("first_id") or (max(int(m.get("mail_id", 0)) for m in mails) if mails else 0)
        if not mails or not first_id:
            return 0
        self.destroy_inbox(email, first_id=int(first_id), epin=epin)
        return len(mails)


def _recipient(j: dict) -> str:
    """Địa chỉ thư được gửi tới (tên trường có thể khác nhau giữa các phiên bản API)."""
    for key in ("to", "to_mail", "mail_to", "recipient", "rcpt_to", "envelope_to"):
        v = j.get(key)
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v)
        if v:
            return str(v)
    return ""


def web_url(address: str | None = None) -> str:
    """Link mở trang tempmail.plus. Có ``address`` thì mở thẳng đúng hộp thư đó
    (trang đọc địa chỉ sau dấu ``?``, vd https://tempmail.plus/en/?abc@mailto.plus)."""
    if address and is_valid_address(address):
        return f"{BASE_URL}/en/?{address.strip().lower()}"
    return BASE_URL + "/"
