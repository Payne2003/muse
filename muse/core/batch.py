"""Tạo một lô hộp thư.

Mỗi hộp thư chỉ được tính là "đã tạo" khi TempMail.Plus xác nhận đã lưu cài đặt
(đọc lại ``/api/box`` thấy đúng thời hạn). Mỗi hộp thành công được ghi ngay vào
SQLite, nên nếu dừng giữa chừng (12/20) thì 12 hộp đó vẫn có trong danh sách.

Nếu trang yêu cầu captcha, lô gọi ``solve_captcha()`` (giao diện hiện ô reCAPTCHA cho
người dùng tự tích) rồi gửi lại đúng bước đó kèm mã xác minh và chạy tiếp. Nếu không có
``solve_captcha`` hoặc người dùng huỷ, lô dừng với trạng thái ``captcha``; các hộp đã tạo
vẫn được giữ.
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass
from typing import Callable, TypeVar

from .api import DOMAINS, CaptchaRequired, TempMailClient, TempMailError, random_name
from .secrets import SecretStore
from .storage import Mailbox, Storage

T = TypeVar("T")

STATUS_DONE = "done"
STATUS_STOPPED = "stopped"
STATUS_CAPTCHA = "captcha"
STATUS_ERROR = "error"


@dataclass
class BatchResult:
    batch_id: int
    requested: int
    created: int
    status: str
    message: str = ""


class BatchCreator:
    def __init__(self, client: TempMailClient, storage: Storage, secrets: SecretStore,
                 *, count: int, ttl_minutes: int, pin: str = "", domain: str | None = None,
                 delay: float = 1.0, max_failures: int = 3,
                 solve_captcha: Callable[[], str | None] | None = None):
        if count < 1:
            raise ValueError("Số lượng phải lớn hơn 0")
        self.client = client
        self.storage = storage
        self.secrets = secrets
        self.count = count
        self.ttl = ttl_minutes
        self.pin = pin
        self.domain = domain
        self.delay = delay
        self.max_failures = max_failures
        self.solve_captcha = solve_captcha
        self.captchas_solved = 0
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def _new_address(self) -> str:
        for _ in range(20):
            domain = self.domain or random.choice(DOMAINS)
            addr = f"{random_name()}@{domain}"
            if not self.storage.exists(addr):
                return addr
        raise TempMailError("Không sinh được địa chỉ mới")

    def _captcha_retry(self, call: Callable[[str | None], T], attempts: int = 3) -> T:
        """Gọi ``call(None)``; nếu trang đòi captcha thì xin mã từ người dùng và gọi lại."""
        token: str | None = None
        for _ in range(attempts):
            try:
                return call(token)
            except CaptchaRequired:
                if self.solve_captcha is None or self._stop.is_set():
                    raise
                token = self.solve_captcha()
                if not token:
                    raise CaptchaRequired("Chưa xác minh captcha")
                self.captchas_solved += 1
        raise CaptchaRequired("TempMail.Plus vẫn yêu cầu captcha sau khi xác minh")

    def create_one(self, batch_id: int | None = None) -> Mailbox:
        address = self._new_address()
        self._captcha_retry(lambda tok: self.client.set_box(
            address, self.ttl, pin=self.pin, recaptcha=tok))
        check = self._captcha_retry(lambda tok: self.client.get_box(
            address, epin=self.pin, recaptcha=tok))
        if check.ttl_minutes != self.ttl:
            raise TempMailError("TempMail.Plus chưa xác nhận cài đặt")
        secret = ""
        try:
            secret = self._captcha_retry(lambda tok: self.client.get_secret_address(
                address, epin=self.pin, recaptcha=tok)) or ""
        except CaptchaRequired:
            raise
        except TempMailError:
            secret = ""  # địa chỉ bí mật có thể lấy lại sau
        if self.pin:
            self.secrets.set(address, "pin", self.pin)
        if secret:
            self.secrets.set(address, "secret", secret)
        return self.storage.add_mailbox(
            address, ttl_minutes=self.ttl, has_pin=bool(self.pin), has_secret=bool(secret),
            applied=True, source="batch", batch_id=batch_id)

    def run(self, on_progress: Callable[[int, int, Mailbox | None], None] | None = None,
            on_log: Callable[[str], None] | None = None) -> BatchResult:
        log = on_log or (lambda m: None)
        batch = self.storage.start_batch(self.count, self.ttl, bool(self.pin))
        created = 0
        failures = 0
        status, message = STATUS_DONE, ""
        if on_progress:
            on_progress(0, self.count, None)
        while created < self.count:
            if self._stop.is_set():
                status, message = STATUS_STOPPED, "Đã dừng theo yêu cầu"
                break
            try:
                mb = self.create_one(batch.id)
            except CaptchaRequired as exc:
                if self._stop.is_set():
                    status, message = STATUS_STOPPED, "Đã dừng theo yêu cầu"
                    break
                status, message = STATUS_CAPTCHA, str(exc)
                log(message)
                break
            except TempMailError as exc:
                failures += 1
                log(f"Lỗi: {exc} (lần {failures}/{self.max_failures})")
                if failures >= self.max_failures:
                    status, message = STATUS_ERROR, str(exc)
                    break
                self._sleep(self.delay * 2)
                continue
            failures = 0
            created = self.storage.batch_increment(batch.id)
            log(f"Đã tạo {mb.address}")
            if on_progress:
                on_progress(created, self.count, mb)
            if created < self.count:
                self._sleep(self.delay)
        self.storage.finish_batch(batch.id, status, message)
        return BatchResult(batch.id, self.count, created, status, message)

    def _sleep(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end and not self._stop.is_set():
            time.sleep(min(0.1, max(0.0, end - time.monotonic())))
