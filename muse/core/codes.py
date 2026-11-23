"""Nhận diện thư xác nhận và tách mã (OTP) ra khỏi nội dung thư."""

from __future__ import annotations

import html as html_mod
import re
from dataclasses import dataclass

KEYWORDS = (
    "mã xác nhận", "mã xác minh", "mã kích hoạt", "mã bảo mật", "mã đăng nhập", "mã otp",
    "xác nhận", "xác minh", "otp", "verification", "verify", "confirmation", "confirm",
    "security code", "login code", "one-time", "one time", "passcode", "your code",
    "code", "kod", "código", "код",
)

# mã đứng sau từ khoá, ví dụ "Mã xác nhận: 089574", "code is 123-456", "FB-12345"
_NEAR = re.compile(
    r"(?<!\w)(?:m[ãa]\s*(?:x[áa]c\s*(?:nh[ậa]n|minh)|k[íi]ch\s*ho[ạa]t|b[ảa]o\s*m[ậa]t|đăng\s*nh[ậa]p|otp)?"
    r"|code|otp|passcode|verification|confirmation)(?!\w)"
    r"[^0-9]{0,40}?((?:[A-Z]{1,3}-)?\d{3}[ -]?\d{1,5})\b",
    re.IGNORECASE,
)
_DIGITS = re.compile(r"(?<![\d.,:/-])(\d{4,8})(?![\d.,:/-])")


@dataclass
class Verification:
    code: str | None
    is_verification: bool


def html_to_text(value: str) -> str:
    value = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", value or "")
    value = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|td|h\d|li)>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"[ \t\r\f\v]+", " ", html_mod.unescape(value))


def _looks_like_year(code: str) -> bool:
    return len(code) == 4 and code[:2] in ("19", "20")


def extract(subject: str, text: str = "", html: str = "") -> Verification:
    body = (text or "") + "\n" + html_to_text(html)
    whole = f"{subject}\n{body}"
    low = whole.lower()
    is_ver = any(k in low for k in KEYWORDS)

    # 1. mã đứng ngay sau từ khoá (ưu tiên tiêu đề, rồi nội dung)
    for src in (subject, body):
        m = _NEAR.search(src or "")
        if m:
            code = m.group(1).replace(" ", "")
            if not _looks_like_year(code):
                return Verification(code, True)

    if not is_ver:
        return Verification(None, False)

    # 2. thư có từ khoá xác nhận: lấy số 4–8 chữ số đầu tiên trong tiêu đề, rồi trong nội dung
    for src in (subject, body):
        for m in _DIGITS.finditer(src or ""):
            code = m.group(1)
            if not _looks_like_year(code):
                return Verification(code, True)
    return Verification(None, True)
