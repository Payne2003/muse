"""Quét nền tất cả hộp thư để phát hiện thư xác nhận mới (không phụ thuộc Qt).

"Đúng tên tài khoản": thư được gửi tới địa chỉ nào thì tài khoản dùng địa chỉ đó.
Nếu thư gửi tới **địa chỉ bí mật** của hộp (địa chỉ bạn dùng để đăng ký tài khoản), thông
báo ghi địa chỉ bí mật đó, kèm hộp thư chính đang chứa thư.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from .api import TempMailClient, TempMailError
from .codes import extract
from .secrets import SecretStore
from .storage import Mailbox, Storage

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


@dataclass
class CodeEvent:
    id: int
    mailbox: str        # hộp thư chính đang chứa thư
    account: str        # địa chỉ tài khoản đang dùng (thư được gửi tới)
    via_secret: bool    # True nếu account là địa chỉ bí mật của hộp
    sender: str
    subject: str
    code: str | None
    mail_id: int


def resolve_account(mailbox: str, secret: str, to_field: str, body: str = "") -> tuple[str, bool]:
    """Xác định thư được gửi tới địa chỉ nào: địa chỉ bí mật hay địa chỉ chính."""
    mailbox, secret = mailbox.lower(), (secret or "").lower()
    recipients = [e.lower() for e in _EMAIL.findall(to_field or "")]
    if secret and secret in recipients:
        return secret, True
    if mailbox in recipients:
        return mailbox, False
    if recipients:
        return recipients[0], bool(secret) and recipients[0] == secret
    # trường "Đến" trống: dựa vào nội dung thư (vd "Thư này đã được gửi đến ...")
    found = [e.lower() for e in _EMAIL.findall(body or "")]
    if secret and secret in found:
        return secret, True
    return mailbox, False


class MailWatcher:
    def __init__(self, client: TempMailClient, storage: Storage, secrets: SecretStore,
                 pause: float = 0.4):
        self.client = client
        self.storage = storage
        self.secrets = secrets
        self.pause = pause

    def _active(self, mb: Mailbox) -> bool:
        exp = mb.expires_at
        return exp is None or exp > datetime.now()

    def scan_once(self, should_stop: Callable[[], bool] = lambda: False) -> list[CodeEvent]:
        events: list[CodeEvent] = []
        for mb in self.storage.list_mailboxes():
            if should_stop():
                break
            if not self._active(mb):
                continue
            try:
                events.extend(self.scan_mailbox(mb))
            except TempMailError:
                continue  # lỗi mạng/PIN của một hộp không làm dừng cả vòng quét
            if self.pause:
                time.sleep(self.pause)
        return events

    def scan_mailbox(self, mb: Mailbox) -> list[CodeEvent]:
        address = mb.address
        pin = self.secrets.pin(address)
        mails = self.client.list_mails(address, epin=pin)
        ids = [m.mail_id for m in mails]
        if not self.storage.is_scanned(address):
            # lần đầu quét hộp này: ghi nhận thư cũ, không báo để tránh dồn thông báo
            self.storage.mark_scanned(address, ids)
            return []
        seen = self.storage.seen_ids(address)
        events = []
        for m in sorted((m for m in mails if m.mail_id not in seen), key=lambda m: m.mail_id):
            d = self.client.get_mail(address, m.mail_id, epin=pin)
            self.storage.mark_scanned(address, [m.mail_id])
            ver = extract(d.subject, d.text, d.html)
            if not ver.is_verification:
                continue
            raw = json.dumps(d.raw, ensure_ascii=False) if d.raw else ""
            account, via_secret = resolve_account(address, self.secrets.secret(address), d.to,
                                                  f"{raw} {d.text} {d.html}")
            sender = f"{d.sender_name} <{d.sender}>" if d.sender_name else d.sender
            cid = self.storage.add_code(mailbox=address, account=account, via_secret=via_secret,
                                        sender=sender, subject=d.subject, code=ver.code,
                                        mail_id=m.mail_id)
            events.append(CodeEvent(cid, address, account, via_secret, sender, d.subject,
                                    ver.code, m.mail_id))
        return events
