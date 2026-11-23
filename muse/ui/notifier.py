"""Thông báo Windows khi hộp thư nhận được thư xác nhận mới."""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QGuiApplication, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QMenu,
                               QPushButton, QSystemTrayIcon, QTableWidget, QTableWidgetItem,
                               QVBoxLayout)

from muse import APP_NAME
from muse.core.watcher import CodeEvent, MailWatcher

from .workers import run_async


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        r = size * 0.18
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, size, size), r, r)
        p.fillPath(path, QColor("#2f6fde"))
        m = size * 0.2
        env = QRectF(m, size * 0.3, size - 2 * m, size * 0.4)
        p.setBrush(QColor("white"))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(env, size * 0.05, size * 0.05)
        pen = QPen(QColor("#2f6fde"), max(1.0, size * 0.06))
        p.setPen(pen)
        p.drawLine(env.topLeft(), env.center())
        p.drawLine(env.center(), env.topRight())
        p.end()
        icon.addPixmap(pm)
    return icon


def sender_label(sender: str) -> str:
    """'Meta <security@account.meta.com>' -> 'Meta'; 'a@b.com' -> 'b.com'."""
    if "<" in sender and sender.split("<")[0].strip():
        return sender.split("<")[0].strip()
    if "@" in sender:
        return sender.split("@")[-1].strip("> ")
    return sender or "Không rõ"


def account_line(account: str, via_secret: bool, mailbox: str) -> str:
    if via_secret:
        return f"Tài khoản: {account} (địa chỉ bí mật)\nHộp thư: {mailbox}"
    return f"Tài khoản: {account}"


class CodesDialog(QDialog):
    """Danh sách mã xác nhận gần đây, phòng khi lỡ thông báo."""

    def __init__(self, storage, on_open_mailbox, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Mã xác nhận gần đây")
        self.resize(900, 460)
        self.storage = storage
        self.on_open_mailbox = on_open_mailbox
        lay = QVBoxLayout(self)
        hint = QLabel("Cột <b>Tài khoản</b> là địa chỉ mà thư được gửi tới (địa chỉ bí mật nếu "
                      "bạn đăng ký bằng địa chỉ bí mật). Bấm đúp vào một dòng để sao chép mã.")
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        lay.addWidget(hint)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Thời gian", "Mã", "Tài khoản", "Hộp thư",
                                              "Người gửi", "Tiêu đề"])
        h = self.table.horizontalHeader()
        for i in range(5):
            h.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(5, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.cellDoubleClicked.connect(lambda *_: self._copy())
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.info = QLabel()
        self.info.setObjectName("muted")
        copy = QPushButton("Sao chép mã")
        copy.setObjectName("primary")
        copy.clicked.connect(self._copy)
        acc = QPushButton("Sao chép tài khoản")
        acc.clicked.connect(lambda: self._copy(col="account"))
        open_btn = QPushButton("Mở hộp thư")
        open_btn.clicked.connect(self._open)
        close = QPushButton("Đóng")
        close.clicked.connect(self.accept)
        row.addWidget(self.info, 1)
        for b in (copy, acc, open_btn, close):
            row.addWidget(b)
        lay.addLayout(row)
        self.rows: list[dict] = []
        self.reload()

    def reload(self) -> None:
        self.rows = self.storage.recent_codes()
        self.table.setRowCount(len(self.rows))
        for r, c in enumerate(self.rows):
            account = c["account"] + ("  (bí mật)" if c["via_secret"] else "")
            vals = [c["received_at"], c["code"] or "—", account, c["mailbox"],
                    sender_label(c["sender"]), c["subject"]]
            for col, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                if col == 1:
                    f = it.font()
                    f.setBold(True)
                    f.setPointSize(f.pointSize() + 1)
                    it.setFont(f)
                self.table.setItem(r, col, it)
        if self.rows:
            self.table.selectRow(0)
        self.info.setText(f"{len(self.rows)} thư xác nhận")

    def _current(self) -> dict | None:
        r = self.table.currentRow()
        return self.rows[r] if 0 <= r < len(self.rows) else None

    def _copy(self, col: str = "code") -> None:
        c = self._current()
        if c and c.get(col):
            QGuiApplication.clipboard().setText(c[col])
            self.info.setText(f"Đã sao chép {c[col]}")

    def _open(self) -> None:
        c = self._current()
        if c:
            self.on_open_mailbox(c["mailbox"])
            self.accept()


class CodeNotifier(QObject):
    """Quét định kỳ tất cả hộp thư và hiện thông báo khi có thư xác nhận mới."""

    new_codes = Signal(list)

    def __init__(self, window, client, storage, secrets):
        super().__init__(window)
        self.window = window
        self.storage = storage
        self.watcher = MailWatcher(client, storage, secrets)
        self._stop = threading.Event()
        self._running = False
        self._last: list[CodeEvent] = []

        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip(APP_NAME)
        menu = QMenu()
        for text, slot in (("Mở Muse Mailbox", window.show_from_tray),
                           ("Mã xác nhận gần đây", window.show_codes),
                           ("Kiểm tra thư ngay", lambda: self.scan_now()),
                           (None, None),
                           ("Thoát", window.quit_app)):
            if text is None:
                menu.addSeparator()
                continue
            act = QAction(text, menu)
            act.triggered.connect(slot)
            menu.addAction(act)
        self._menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_activated)
        self.tray.messageClicked.connect(self._on_message_clicked)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.scan_now)

    # --------------------------------------------------------------- settings
    def _setting(self, key: str, default: str) -> str:
        return self.storage.get_setting(key, default)

    @property
    def enabled(self) -> bool:
        return self._setting("notify_enabled", "1") == "1"

    @property
    def interval_s(self) -> int:
        try:
            return max(20, int(self._setting("notify_interval", "60")))
        except ValueError:
            return 60

    @property
    def auto_copy(self) -> bool:
        return self._setting("notify_auto_copy", "0") == "1"

    def start(self) -> None:
        self.timer.stop()
        if self.enabled:
            self.timer.start(5000)

    def shutdown(self) -> None:
        self._stop.set()
        self.timer.stop()
        self.tray.hide()

    # --------------------------------------------------------------- scanning
    def scan_now(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop.clear()

        def done(events):
            self._running = False
            if events:
                self._notify(events)
                self.new_codes.emit(events)
            self._schedule()

        def failed(_exc):
            self._running = False
            self._schedule()

        run_async(lambda: self.watcher.scan_once(self._stop.is_set), done, failed)

    def _schedule(self) -> None:
        if self.enabled and not self._stop.is_set():
            self.timer.start(self.interval_s * 1000)

    # --------------------------------------------------------------- notify
    def _notify(self, events: list[CodeEvent]) -> None:
        self._last = events
        first = events[0]
        if self.auto_copy and first.code:
            QGuiApplication.clipboard().setText(first.code)
        if len(events) == 1:
            e = first
            src = sender_label(e.sender)
            title = f"Mã {e.code} · {src}" if e.code else f"Thư xác nhận mới · {src}"
            body = account_line(e.account, e.via_secret, e.mailbox)
            if not e.code:
                body += f"\n{e.subject}"
            body += "\n" + ("Đã sao chép mã." if self.auto_copy and e.code
                            else "Bấm vào đây để sao chép mã.")
        else:
            title = f"{len(events)} thư xác nhận mới"
            lines = [f"{e.code or '—'} · {sender_label(e.sender)} → {e.account}" for e in events[:4]]
            if len(events) > 4:
                lines.append(f"… và {len(events) - 4} thư khác")
            body = "\n".join(lines) + "\nBấm để xem danh sách."
        if self.tray.isVisible():
            self.tray.showMessage(title, body, QSystemTrayIcon.Information, 20000)
        else:  # không có khay hệ thống: dùng thanh trạng thái
            self.window.statusBar().showMessage(title + " — " + body.replace("\n", " · "), 20000)

    def _on_message_clicked(self) -> None:
        if len(self._last) == 1:
            e = self._last[0]
            if e.code:
                QGuiApplication.clipboard().setText(e.code)
            self.window.show_from_tray()
            self.window.open_mailbox(e.mailbox)
            if e.code:
                self.window.statusBar().showMessage(
                    f"Đã sao chép mã {e.code} cho tài khoản {e.account}", 8000)
        else:
            self.window.show_from_tray()
            self.window.show_codes()

    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.window.show_from_tray()
