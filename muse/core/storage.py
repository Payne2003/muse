"""SQLite: danh sách hộp thư và tiến độ các lô tạo hộp thư.

PIN và địa chỉ bí mật KHÔNG lưu ở đây (xem secrets.py); bảng chỉ có cờ
``has_pin``/``has_secret`` để hiển thị trạng thái.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS mailboxes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    address     TEXT NOT NULL UNIQUE COLLATE NOCASE,
    ttl_minutes INTEGER,
    has_pin     INTEGER NOT NULL DEFAULT 0,
    has_secret  INTEGER NOT NULL DEFAULT 0,
    applied     INTEGER NOT NULL DEFAULT 0,
    applied_at  TEXT,
    source      TEXT NOT NULL DEFAULT 'existing',
    batch_id    INTEGER,
    note        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    last_active TEXT
);
CREATE TABLE IF NOT EXISTS batches (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    requested   INTEGER NOT NULL,
    created     INTEGER NOT NULL DEFAULT 0,
    ttl_minutes INTEGER NOT NULL,
    with_pin    INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'running',
    message     TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def app_home() -> Path:
    env = os.environ.get("MUSE_HOME")
    if env:
        p = Path(env)
    elif os.name == "nt":
        p = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "MuseMailbox"
    else:
        p = Path.home() / ".muse_mailbox"
    p.mkdir(parents=True, exist_ok=True)
    return p


@dataclass
class Mailbox:
    id: int
    address: str
    ttl_minutes: int | None
    has_pin: bool
    has_secret: bool
    applied: bool
    applied_at: str | None
    source: str
    batch_id: int | None
    note: str
    created_at: str
    last_active: str | None

    @property
    def expires_at(self) -> datetime | None:
        """Thời điểm ước tính hết hạn: thư bị xoá sau ``ttl`` tính từ lần dùng gần nhất."""
        if not self.ttl_minutes:
            return None
        base = self.last_active or self.applied_at or self.created_at
        try:
            return datetime.fromisoformat(base) + timedelta(minutes=self.ttl_minutes)
        except ValueError:
            return None


@dataclass
class Batch:
    id: int
    requested: int
    created: int
    ttl_minutes: int
    with_pin: bool
    status: str
    message: str
    created_at: str
    finished_at: str | None


class Storage:
    def __init__(self, path: str | Path | None = None):
        self.path = str(path or (app_home() / "mailboxes.db"))
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ------------------------------------------------------------------ mailboxes
    def add_mailbox(self, address: str, *, ttl_minutes: int | None = None, has_pin=False,
                    has_secret=False, applied=False, source="existing",
                    batch_id: int | None = None, note: str = "") -> Mailbox:
        address = address.strip().lower()
        ts = now_iso()
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO mailboxes(address, ttl_minutes, has_pin, has_secret, applied, "
                "applied_at, source, batch_id, note, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (address, ttl_minutes, int(has_pin), int(has_secret), int(applied),
                 ts if applied else None, source, batch_id, note, ts),
            )
            self._db.commit()
            return self.get_mailbox(cur.lastrowid)

    def exists(self, address: str) -> bool:
        with self._lock:
            r = self._db.execute("SELECT 1 FROM mailboxes WHERE address = ?",
                                 (address.strip().lower(),)).fetchone()
        return r is not None

    def get_mailbox(self, mailbox_id: int) -> Mailbox | None:
        with self._lock:
            r = self._db.execute("SELECT * FROM mailboxes WHERE id = ?", (mailbox_id,)).fetchone()
        return self._mb(r) if r else None

    def find(self, address: str) -> Mailbox | None:
        with self._lock:
            r = self._db.execute("SELECT * FROM mailboxes WHERE address = ?",
                                 (address.strip().lower(),)).fetchone()
        return self._mb(r) if r else None

    def list_mailboxes(self, search: str = "") -> list[Mailbox]:
        with self._lock:
            if search:
                rows = self._db.execute(
                    "SELECT * FROM mailboxes WHERE address LIKE ? OR note LIKE ? ORDER BY id DESC",
                    (f"%{search}%", f"%{search}%")).fetchall()
            else:
                rows = self._db.execute("SELECT * FROM mailboxes ORDER BY id DESC").fetchall()
        return [self._mb(r) for r in rows]

    def update_mailbox(self, mailbox_id: int, **fields) -> None:
        allowed = {"ttl_minutes", "has_pin", "has_secret", "applied", "applied_at", "note",
                   "last_active"}
        cols = {k: v for k, v in fields.items() if k in allowed}
        if not cols:
            return
        for k in ("has_pin", "has_secret", "applied"):
            if k in cols:
                cols[k] = int(bool(cols[k]))
        sets = ", ".join(f"{k} = ?" for k in cols)
        with self._lock:
            self._db.execute(f"UPDATE mailboxes SET {sets} WHERE id = ?",
                             (*cols.values(), mailbox_id))
            self._db.commit()

    def delete_mailbox(self, mailbox_id: int) -> None:
        with self._lock:
            self._db.execute("DELETE FROM mailboxes WHERE id = ?", (mailbox_id,))
            self._db.commit()

    @staticmethod
    def _mb(r: sqlite3.Row) -> Mailbox:
        return Mailbox(
            id=r["id"], address=r["address"], ttl_minutes=r["ttl_minutes"],
            has_pin=bool(r["has_pin"]), has_secret=bool(r["has_secret"]),
            applied=bool(r["applied"]), applied_at=r["applied_at"], source=r["source"],
            batch_id=r["batch_id"], note=r["note"], created_at=r["created_at"],
            last_active=r["last_active"],
        )

    # ------------------------------------------------------------------ batches
    def start_batch(self, requested: int, ttl_minutes: int, with_pin: bool) -> Batch:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO batches(requested, ttl_minutes, with_pin, created_at) VALUES (?,?,?,?)",
                (requested, ttl_minutes, int(with_pin), now_iso()))
            self._db.commit()
            return self.get_batch(cur.lastrowid)

    def batch_increment(self, batch_id: int) -> int:
        with self._lock:
            self._db.execute("UPDATE batches SET created = created + 1 WHERE id = ?", (batch_id,))
            self._db.commit()
            return self._db.execute("SELECT created FROM batches WHERE id = ?",
                                    (batch_id,)).fetchone()[0]

    def finish_batch(self, batch_id: int, status: str, message: str = "") -> None:
        with self._lock:
            self._db.execute(
                "UPDATE batches SET status = ?, message = ?, finished_at = ? WHERE id = ?",
                (status, message, now_iso(), batch_id))
            self._db.commit()

    def get_batch(self, batch_id: int) -> Batch | None:
        with self._lock:
            r = self._db.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
        if not r:
            return None
        return Batch(id=r["id"], requested=r["requested"], created=r["created"],
                     ttl_minutes=r["ttl_minutes"], with_pin=bool(r["with_pin"]),
                     status=r["status"], message=r["message"], created_at=r["created_at"],
                     finished_at=r["finished_at"])

    def list_batches(self) -> list[Batch]:
        with self._lock:
            ids = [r[0] for r in self._db.execute("SELECT id FROM batches ORDER BY id DESC")]
        return [self.get_batch(i) for i in ids]

    def mark_interrupted_batches(self) -> None:
        """Lô còn 'running' khi mở lại app nghĩa là app đã bị đóng giữa chừng."""
        with self._lock:
            self._db.execute(
                "UPDATE batches SET status = 'stopped', message = 'Ứng dụng bị đóng giữa chừng', "
                "finished_at = ? WHERE status = 'running'", (now_iso(),))
            self._db.commit()

    # ------------------------------------------------------------------ settings
    def get_setting(self, key: str, default: str = "") -> str:
        with self._lock:
            r = self._db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return r[0] if r else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute("INSERT INTO settings(key, value) VALUES (?, ?) "
                             "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
            self._db.commit()
