"""Lưu PIN và địa chỉ bí mật vào Windows Credential Manager (qua thư viện keyring).

Không bao giờ ghi PIN/địa chỉ bí mật vào SQLite. Khi không có Credential Manager
(ví dụ chạy test trên Linux), dùng bộ nhớ tạm trong RAM.
"""

from __future__ import annotations

import threading

SERVICE = "MuseMailbox"


class SecretStore:
    def __init__(self, backend=None):
        self._mem: dict[tuple[str, str], str] = {}
        self._lock = threading.Lock()
        self._kr = None
        if backend == "memory":
            return
        try:
            import keyring
            from keyring.backends import fail

            kr = backend or keyring.get_keyring()
            if not isinstance(kr, fail.Keyring):
                self._kr = kr
        except Exception:
            self._kr = None

    @property
    def persistent(self) -> bool:
        return self._kr is not None

    def _key(self, address: str, kind: str) -> str:
        return f"{address.lower()}:{kind}"

    def get(self, address: str, kind: str) -> str:
        key = self._key(address, kind)
        with self._lock:
            if self._kr is not None:
                try:
                    return self._kr.get_password(SERVICE, key) or ""
                except Exception:
                    pass
            return self._mem.get((SERVICE, key), "")

    def set(self, address: str, kind: str, value: str) -> None:
        key = self._key(address, kind)
        with self._lock:
            if not value:
                self._delete_locked(key)
                return
            if self._kr is not None:
                try:
                    self._kr.set_password(SERVICE, key, value)
                    return
                except Exception:
                    pass
            self._mem[(SERVICE, key)] = value

    def delete(self, address: str, kind: str) -> None:
        with self._lock:
            self._delete_locked(self._key(address, kind))

    def _delete_locked(self, key: str) -> None:
        self._mem.pop((SERVICE, key), None)
        if self._kr is not None:
            try:
                self._kr.delete_password(SERVICE, key)
            except Exception:
                pass

    # tiện ích
    def pin(self, address: str) -> str:
        return self.get(address, "pin")

    def secret(self, address: str) -> str:
        return self.get(address, "secret")

    def forget(self, address: str) -> None:
        self.delete(address, "pin")
        self.delete(address, "secret")
