"""Quét nền tất cả hộp thư để phát hiện thư xác nhận mới (không phụ thuộc Qt).

"Đúng tên tài khoản": thư được gửi tới địa chỉ nào thì tài khoản dùng địa chỉ đó.
Nếu thư gửi tới **địa chỉ bí mật** của hộp (địa chỉ bạn dùng để đăng ký tài khoản), thông
báo ghi địa chỉ bí mật đó, kèm hộp thư chính đang chứa thư.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from .api import TempMailClient, TempMailError
from .codes import extract
from .services import classify, identify_service
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
    service: str = ""
    newly_registered: bool = False


@dataclass
class RegistrationEvent:
    mailbox: str
    service: str
    account: str
    via_secret: bool
    kind: str
    subject: str


@dataclass
class ScanResult:
    codes: list[CodeEvent] = field(default_factory=list)
    registrations: list[RegistrationEvent] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.codes or self.registrations)


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

    def scan_once(self, should_stop: Callable[[], bool] = lambda: False) -> ScanResult:
        events = ScanResult()
        backfill = self.storage.get_setting("reg_backfill_done", "0") != "1"
        for mb in self.storage.list_mailboxes():
            if should_stop():
                break
            if not self._active(mb):
                continue
            try:
                if backfill and self.storage.is_scanned(mb.address):
                    self.backfill_mailbox(mb)  # hộp đã quét từ bản cũ: nhận diện thư sẵn có
                res = self.scan_mailbox(mb)
                events.codes.extend(res.codes)
                events.registrations.extend(res.registrations)
            except TempMailError:
                continue  # lỗi mạng/PIN của một hộp không làm dừng cả vòng quét
            if self.pause:
                time.sleep(self.pause)
        else:
            if backfill:
                self.storage.set_setting("reg_backfill_done", "1")
        return events

    def backfill_mailbox(self, mb: Mailbox) -> int:
        """Một lần: nhận diện đăng ký từ các thư ĐÃ CÓ (chỉ dùng tiêu đề + người gửi), không báo."""
        mails = self.client.list_mails(mb.address, epin=self.secrets.pin(mb.address), limit=100)
        n = 0
        for m in mails:
            kind = classify(m.subject)
            if kind:
                service = identify_service(m.sender, m.sender_name)
                n += self.storage.add_registration(
                    mailbox=mb.address, service=service, kind=kind, mail_id=m.mail_id,
                    subject=m.subject)
        return n

    def scan_mailbox(self, mb: Mailbox) -> ScanResult:
        address = mb.address
        pin = self.secrets.pin(address)
        mails = self.client.list_mails(address, epin=pin)
        ids = [m.mail_id for m in mails]
        if not self.storage.is_scanned(address):
            # lần đầu quét hộp này: ghi nhận thư cũ, không báo để tránh dồn thông báo
            self.storage.mark_scanned(address, ids)
            self.backfill_mailbox(mb)
            return ScanResult()
        seen = self.storage.seen_ids(address)
        events = ScanResult()
        for m in sorted((m for m in mails if m.mail_id not in seen), key=lambda m: m.mail_id):
            d = self.client.get_mail(address, m.mail_id, epin=pin)
            self.storage.mark_scanned(address, [m.mail_id])
            ver = extract(d.subject, d.text, d.html)
            kind = classify(d.subject, d.text, d.html)
            if not ver.is_verification and not kind:
                continue
            raw = json.dumps(d.raw, ensure_ascii=False) if d.raw else ""
            account, via_secret = resolve_account(address, self.secrets.secret(address), d.to,
                                                  f"{raw} {d.text} {d.html}")
            sender = f"{d.sender_name} <{d.sender}>" if d.sender_name else d.sender
            service = identify_service(d.sender, d.sender_name)
            new_reg = False
            if kind:
                new_reg = self.storage.add_registration(
                    mailbox=address, service=service, account=account, via_secret=via_secret,
                    kind=kind, mail_id=m.mail_id, subject=d.subject)
            if ver.is_verification:
                cid = self.storage.add_code(mailbox=address, account=account,
                                            via_secret=via_secret, sender=sender,
                                            subject=d.subject, code=ver.code, mail_id=m.mail_id)
                events.codes.append(CodeEvent(cid, address, account, via_secret, sender,
                                              d.subject, ver.code, m.mail_id, service, new_reg))
            elif new_reg:
                events.registrations.append(RegistrationEvent(
                    address, service, account, via_secret, kind, d.subject))
        return events
