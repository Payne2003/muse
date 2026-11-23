"""Nhận biết thư nào cho thấy hộp thư đã được dùng để đăng ký một dịch vụ, và dịch vụ nào."""

from __future__ import annotations

import re

from .codes import extract

# đuôi tên miền người gửi -> tên dịch vụ
SERVICE_DOMAINS: dict[str, str] = {
    "facebookmail.com": "Facebook", "facebook.com": "Facebook", "meta.com": "Meta",
    "instagram.com": "Instagram", "mail.instagram.com": "Instagram", "threads.net": "Threads",
    "whatsapp.com": "WhatsApp", "google.com": "Google", "youtube.com": "YouTube",
    "microsoft.com": "Microsoft", "live.com": "Microsoft", "outlook.com": "Microsoft",
    "apple.com": "Apple", "icloud.com": "Apple", "amazon.com": "Amazon", "x.com": "X",
    "twitter.com": "X", "tiktok.com": "TikTok", "tiktokv.com": "TikTok", "discord.com": "Discord",
    "telegram.org": "Telegram", "linkedin.com": "LinkedIn", "github.com": "GitHub",
    "openai.com": "OpenAI", "anthropic.com": "Anthropic", "netflix.com": "Netflix",
    "spotify.com": "Spotify", "shopee.vn": "Shopee", "shopee.com": "Shopee", "lazada.vn": "Lazada",
    "tiki.vn": "Tiki", "zalo.me": "Zalo", "vng.com.vn": "Zalo", "grab.com": "Grab",
    "paypal.com": "PayPal", "binance.com": "Binance", "reddit.com": "Reddit",
    "pinterest.com": "Pinterest", "snapchat.com": "Snapchat", "canva.com": "Canva",
    "steampowered.com": "Steam", "epicgames.com": "Epic Games", "yahoo.com": "Yahoo",
}

WELCOME = ("chào mừng", "tài khoản của bạn đã được tạo", "đăng ký thành công", "cảm ơn bạn đã đăng ký",
           "welcome to", "welcome,", "your account has been created", "account created",
           "thanks for signing up", "thank you for signing up", "thanks for registering",
           "get started with", "activate your account", "kích hoạt tài khoản")
LOGIN = ("đăng nhập mới", "lần đăng nhập", "new login", "new sign-in", "sign-in attempt",
         "security alert", "cảnh báo bảo mật", "password reset", "đặt lại mật khẩu",
         "reset your password", "login attempt", "unusual activity")

_EMAIL = re.compile(r"([A-Za-z0-9._%+\-]+)@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})")
_GENERIC_NAMES = {"", "no-reply", "noreply", "support", "info", "team", "notification",
                  "notifications", "security", "account", "accounts", "mail", "admin"}

KIND_VERIFY, KIND_WELCOME, KIND_LOGIN = "verify", "welcome", "login"
KIND_LABEL = {KIND_VERIFY: "thư xác nhận", KIND_WELCOME: "thư chào mừng",
              KIND_LOGIN: "thư đăng nhập/bảo mật", "manual": "tự đánh dấu"}


def identify_service(sender: str, sender_name: str = "") -> str:
    """'security@account.meta.com' -> 'Meta'. Dịch vụ lạ: tên người gửi, rồi tên miền gốc."""
    m = _EMAIL.search(sender or "")
    domain = m.group(2).lower() if m else ""
    parts = domain.split(".")
    for i in range(len(parts) - 1):
        name = SERVICE_DOMAINS.get(".".join(parts[i:]))
        if name:
            return name
    name = (sender_name or "").strip().strip('"')
    if name and name.lower() not in _GENERIC_NAMES:
        return name[:40]
    if len(parts) >= 2:
        base = parts[-3] if len(parts) >= 3 and len(parts[-2]) <= 3 else parts[-2]
        return base.capitalize()
    return "Không rõ"


def classify(subject: str, text: str = "", html: str = "") -> str | None:
    """Trả về loại thư cho thấy đã đăng ký (verify/welcome/login), hoặc None."""
    low = f"{subject}\n{text}\n{html}".lower()
    if any(k in low for k in WELCOME):
        return KIND_WELCOME
    if extract(subject, text, html).is_verification:
        return KIND_VERIFY
    if any(k in low for k in LOGIN):
        return KIND_LOGIN
    return None
