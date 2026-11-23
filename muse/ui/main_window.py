from __future__ import annotations

import html as html_mod
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QFrame, QSystemTrayIcon, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QComboBox, QFileDialog, QInputDialog, QMenu, QTabBar,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPushButton, QSizePolicy, QSplitter,
    QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget,
)

from muse import APP_NAME, __version__
from muse.core.api import LIFETIMES, CaptchaRequired, TempMailClient, TempMailError, WrongPin, web_url
from muse.core.secrets import SecretStore
from muse.core.export import format_list
from muse.core.services import KIND_LABEL
from muse.core.storage import Mailbox, Storage, now_iso

from .dialogs import (AddExistingDialog, AppSettings, ApplySettingsDialog, BatchDialog,
                      ManualCreateDialog, SettingsDialog)
from .notifier import CodeNotifier, CodesDialog, app_icon
from .updater_ui import UpdateController
from .workers import run_async

REFRESH_MS = 20_000


def mask(value: str) -> str:
    """Ẩn địa chỉ bí mật: ab••••••@domain."""
    if not value:
        return "—"
    if "@" in value:
        name, domain = value.split("@", 1)
        return f"{name[:2]}{'•' * 6}@{domain}"
    return "•" * 6


def fmt_dt(value: str | datetime | None) -> str:
    if not value:
        return "—"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    return value.strftime("%d/%m/%Y %H:%M")


def badge(text: str, kind: str) -> QLabel:
    lb = QLabel(text)
    lb.setObjectName({"ok": "badgeOk", "warn": "badgeWarn"}.get(kind, "badgeOff"))
    return lb


class MailboxRow(QWidget):
    """Một dòng trong danh sách bên trái: địa chỉ + nút Sao chép."""

    def __init__(self, mb: Mailbox, on_copy, regs: list | None = None):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 6, 6)
        col = QVBoxLayout()
        col.setSpacing(1)
        addr = QLabel(mb.address)
        addr.setStyleSheet("font-weight:600; color:#12355b;")
        if regs:
            names = " · ".join(r["service"] for r in regs)
            sub = QLabel(f"✓ {names} · {fmt_dt(regs[0]['created_at'])[:10]}")
            sub.setStyleSheet("font-size:8.5pt; color:#1b7a3d; font-weight:600;")
        elif mb.source == "quick" and not mb.applied:
            sub = QLabel("Tạo nhanh · thời hạn mặc định của trang")
        else:
            ttl = LIFETIMES.get(mb.ttl_minutes or 0, "chưa rõ thời hạn")
            state = "Đã áp dụng" if mb.applied else "Chưa áp dụng"
            pin = " · PIN" if mb.has_pin else ""
            sub = QLabel(f"{ttl} · {state}{pin}")
        if not regs:
            sub.setObjectName("muted")
            sub.setStyleSheet("font-size:8.5pt;")
        col.addWidget(addr)
        col.addWidget(sub)
        lay.addLayout(col, 1)
        btn = QPushButton("Sao chép")
        btn.setObjectName("small")
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda: on_copy(mb.address))
        lay.addWidget(btn)


class MainWindow(QMainWindow):
    def __init__(self, storage: Storage, secrets: SecretStore, client: TempMailClient | None = None):
        super().__init__()
        self.storage = storage
        self.secrets = secrets
        self.client = client or TempMailClient()
        self.settings = AppSettings(storage)
        self.current: Mailbox | None = None
        self._mail_ids: list[int] = []
        self._show_secret = False
        self._show_pin = False
        self._req = 0  # bỏ qua kết quả trả về muộn của hộp thư cũ

        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1200, 760)
        self._build()
        self.reload_list()
        self.timer = QTimer(self)
        self.timer.setInterval(REFRESH_MS)
        self.timer.timeout.connect(lambda: self.refresh_mails(silent=True))
        self.timer.start()
        self.updater = UpdateController(storage, self)
        self.updater.available.connect(self._on_update_available)
        self.updater.start()
        self._quitting = False
        self._tray_hint_shown = False
        self._codes_dlg = None
        self.setWindowIcon(app_icon())
        QApplication.instance().setWindowIcon(app_icon())
        QApplication.instance().setQuitOnLastWindowClosed(False)
        self.notifier = CodeNotifier(self, self.client, storage, secrets)
        self.notifier.new_codes.connect(self._on_new_codes)
        self.notifier.start()

    def _on_update_available(self, info) -> None:
        self.update_act.setText(f"⬆ Cập nhật lên v{info.version}")
        self.update_act.setVisible(True)

    # ================================================================ layout
    def _build(self) -> None:
        tb = self.addToolBar("Chính")
        tb.setMovable(False)
        for text, slot in (("Thêm hộp thư đang có", self.add_existing),
                           ("Tạo một lô", self.create_batch),
                           ("Tạo thủ công", self.create_manual),
                           ("Mã xác nhận", self.show_codes),
                           ("Cài đặt", self.open_settings)):
            act = QAction(text, self)
            act.triggered.connect(slot)
            tb.addAction(act)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        self.update_act = QAction("Cập nhật", self)
        self.update_act.triggered.connect(lambda: self.updater.prompt())
        tb.addAction(self.update_act)
        self.update_act.setVisible(False)
        check = QAction("Kiểm tra cập nhật", self)
        check.triggered.connect(lambda: self.updater.check(silent=False))
        tb.addAction(check)

        split = QSplitter()
        split.setChildrenCollapsible(False)
        self.setCentralWidget(split)

        # ---- trái: danh sách hộp thư
        side = QWidget()
        side.setObjectName("sidebar")
        sl = QVBoxLayout(side)
        sl.setContentsMargins(10, 12, 10, 10)
        title = QLabel("Hộp thư trên máy")
        title.setObjectName("h2")
        sl.addWidget(title)
        self.tabs = QTabBar()
        self.tabs.addTab("Chưa dùng")
        self.tabs.addTab("Đã đăng ký")
        self.tabs.setExpanding(True)
        self.tabs.setDrawBase(False)
        self.tabs.currentChanged.connect(self._on_tab)
        sl.addWidget(self.tabs)
        reg_row = QHBoxLayout()
        self.service_filter = QComboBox()
        self.service_filter.currentIndexChanged.connect(lambda _: self.reload_list())
        reg_row.addWidget(self.service_filter, 1)
        self.reg_bar = QWidget()
        self.reg_bar.setLayout(reg_row)
        reg_row.setContentsMargins(0, 0, 0, 0)
        self.reg_bar.setVisible(False)
        sl.addWidget(self.reg_bar)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Tìm địa chỉ…")
        self.search.textChanged.connect(lambda: self.reload_list())
        sl.addWidget(self.search)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._on_select)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._list_menu)
        sl.addWidget(self.list, 1)
        self.count_label = QLabel()
        self.count_label.setObjectName("muted")
        foot = QHBoxLayout()
        foot.addWidget(self.count_label, 1)
        self.export_btn = QPushButton("Xuất danh sách ▾")
        self.export_btn.setToolTip("Xuất các hộp thư đang hiện trong danh sách (theo thẻ và bộ lọc)")
        exp_menu = QMenu(self.export_btn)
        exp_menu.addAction("Lưu file .txt (STT · Available · email | link · Xóa)", self.export_txt)
        exp_menu.addAction("Sao chép danh sách vào clipboard", self.copy_list)
        exp_menu.addSeparator()
        exp_menu.addAction("Lưu CSV chi tiết (dịch vụ, ngày, địa chỉ bí mật)", self.export_registered)
        self.export_btn.setMenu(exp_menu)
        foot.addWidget(self.export_btn)
        sl.addLayout(foot)
        split.addWidget(side)

        # ---- phải: chi tiết + thư
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(14, 12, 14, 12)
        rl.setSpacing(10)

        self.card = QFrame()
        self.card.setObjectName("card")
        cl = QVBoxLayout(self.card)
        head = QHBoxLayout()
        self.addr_label = QLabel("Chọn một địa chỉ để mở hộp thư")
        self.addr_label.setObjectName("title")
        self.addr_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        head.addWidget(self.addr_label, 1)
        self.copy_btn = QPushButton("Sao chép")
        self.copy_btn.clicked.connect(lambda: self.current and self.copy(self.current.address))
        head.addWidget(self.copy_btn)
        cl.addLayout(head)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(6)
        self.ttl_value = QLabel("—")
        self.exp_value = QLabel("—")
        self.state_box = QHBoxLayout()
        self.pin_value = QLabel("—")
        self.pin_toggle = QPushButton("Hiện")
        self.pin_toggle.setObjectName("small")
        self.pin_toggle.clicked.connect(self._toggle_pin)
        self.secret_value = QLabel("—")
        self.secret_value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.secret_toggle = QPushButton("Hiện")
        self.secret_toggle.setObjectName("small")
        self.secret_toggle.clicked.connect(self._toggle_secret)
        self.secret_copy = QPushButton("Sao chép")
        self.secret_copy.setObjectName("small")
        self.secret_copy.clicked.connect(self._copy_secret)

        def label(t):
            lb = QLabel(t)
            lb.setObjectName("muted")
            return lb

        grid.addWidget(label("Thời hạn"), 0, 0)
        grid.addWidget(self.ttl_value, 0, 1)
        grid.addWidget(label("Hết hạn (ước tính)"), 0, 2)
        grid.addWidget(self.exp_value, 0, 3)
        grid.addWidget(label("Cài đặt"), 1, 0)
        sw = QWidget()
        sw.setLayout(self.state_box)
        self.state_box.setContentsMargins(0, 0, 0, 0)
        grid.addWidget(sw, 1, 1)
        grid.addWidget(label("PIN"), 1, 2)
        pin_row = QHBoxLayout()
        pin_row.addWidget(self.pin_value)
        pin_row.addWidget(self.pin_toggle)
        pin_row.addStretch(1)
        grid.addLayout(pin_row, 1, 3)
        grid.addWidget(label("Địa chỉ bí mật"), 2, 0)
        sec_row = QHBoxLayout()
        sec_row.addWidget(self.secret_value)
        sec_row.addWidget(self.secret_toggle)
        sec_row.addWidget(self.secret_copy)
        sec_row.addStretch(1)
        grid.addLayout(sec_row, 2, 1, 1, 3)
        grid.addWidget(label("Đăng ký"), 3, 0)
        self.reg_value = QLabel("—")
        self.reg_value.setWordWrap(True)
        self.reg_btn = QPushButton("Đánh dấu đã đăng ký…")
        self.reg_btn.setObjectName("small")
        self.reg_btn.clicked.connect(self._toggle_registered)
        reg_row2 = QHBoxLayout()
        reg_row2.addWidget(self.reg_value, 1)
        reg_row2.addWidget(self.reg_btn)
        grid.addLayout(reg_row2, 3, 1, 1, 3)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        cl.addLayout(grid)

        actions = QHBoxLayout()
        self.refresh_btn = QPushButton("Làm mới thư")
        self.refresh_btn.setObjectName("primary")
        self.refresh_btn.clicked.connect(lambda: self.refresh_mails())
        self.verify_btn = QPushButton("Kiểm tra cài đặt")
        self.verify_btn.clicked.connect(lambda: self.current and self.verify(self.current.address))
        self.apply_btn = QPushButton("Đổi thời hạn / PIN")
        self.apply_btn.clicked.connect(self.change_settings)
        self.web_btn = QPushButton("Mở trên web")
        self.web_btn.setToolTip("Mở đúng hộp thư này trên tempmail.plus")
        self.web_btn.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl(web_url(self.current.address if self.current else None))))
        self.del_btn = QPushButton("Xoá khỏi danh sách")
        self.del_btn.setObjectName("danger")
        self.del_btn.clicked.connect(self.remove_current)
        for b in (self.refresh_btn, self.verify_btn, self.apply_btn, self.web_btn):
            actions.addWidget(b)
        actions.addStretch(1)
        actions.addWidget(self.del_btn)
        cl.addLayout(actions)
        rl.addWidget(self.card)

        msplit = QSplitter(Qt.Vertical)
        self.mails = QTableWidget(0, 3)
        self.mails.setHorizontalHeaderLabels(["Người gửi", "Tiêu đề", "Thời gian"])
        self.mails.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.mails.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.mails.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.mails.verticalHeader().setVisible(False)
        self.mails.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.mails.setSelectionMode(QAbstractItemView.SingleSelection)
        self.mails.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.mails.itemSelectionChanged.connect(self._open_mail)
        self.viewer = QTextBrowser()
        self.viewer.setOpenExternalLinks(True)
        self.viewer.setPlaceholderText("Chọn một thư để đọc.")
        msplit.addWidget(self.mails)
        msplit.addWidget(self.viewer)
        msplit.setSizes([260, 380])
        rl.addWidget(msplit, 1)
        split.addWidget(right)
        split.setSizes([340, 860])

        self.statusBar().showMessage("Sẵn sàng")
        self._render_detail()

    # ================================================================ list
    def reload_list(self, verify: str | None = None) -> None:
        keep = self.current.id if self.current else None
        self.list.blockSignals(True)
        self.list.clear()
        regmap = self.storage.registered_map()
        registered_tab = self.tabs.currentIndex() == 1
        self._refresh_service_filter()
        service = self.service_filter.currentData() if registered_tab else None
        boxes = []
        for mb in self.storage.list_mailboxes(self.search.text().strip()):
            regs = regmap.get(mb.address)
            if registered_tab != bool(regs):
                continue
            if service and not any(r["service"].lower() == service.lower() for r in regs):
                continue
            boxes.append(mb)
        all_boxes = self.storage.list_mailboxes()
        n_reg = sum(1 for b in all_boxes if b.address in regmap)
        self.tabs.setTabText(0, f"Chưa dùng ({len(all_boxes) - n_reg})")
        self.tabs.setTabText(1, f"Đã đăng ký ({n_reg})")
        select_row = -1
        for i, mb in enumerate(boxes):
            item = QListWidgetItem()
            item.setData(Qt.UserRole, mb.id)
            row = MailboxRow(mb, self.copy, regmap.get(mb.address))
            item.setSizeHint(row.sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
            if mb.id == keep:
                select_row = i
        self.list.blockSignals(False)
        self.count_label.setText(f"Đang hiện {len(boxes)} / tổng {len(all_boxes)} hộp thư")
        self._shown = [mb.address for mb in boxes]
        if select_row >= 0:
            self.list.setCurrentRow(select_row)
            self.current = self.storage.get_mailbox(keep)
            self._render_detail()
        if verify:
            self.verify(verify)

    def _on_select(self, item: QListWidgetItem | None, _prev=None) -> None:
        if item is None:
            return
        mb = self.storage.get_mailbox(item.data(Qt.UserRole))
        if not mb:
            return
        if not self.current or self.current.id != mb.id:
            self._show_pin = self._show_secret = False
            self.mails.setRowCount(0)
            self.viewer.clear()
            self._mail_ids = []
        self.current = mb
        self._render_detail()
        self.refresh_mails()

    def _render_detail(self) -> None:
        mb = self.current
        enabled = mb is not None
        for w in (self.copy_btn, self.refresh_btn, self.verify_btn, self.apply_btn,
                  self.del_btn, self.pin_toggle, self.secret_toggle, self.secret_copy,
                  self.reg_btn):
            w.setEnabled(enabled)
        while self.state_box.count():
            w = self.state_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        if not mb:
            self.addr_label.setText("Chọn một địa chỉ để mở hộp thư")
            return
        self.addr_label.setText(mb.address)
        self.ttl_value.setText(LIFETIMES.get(mb.ttl_minutes or 0, "Chưa rõ"))
        self.exp_value.setText(fmt_dt(mb.expires_at) if mb.applied else "—")
        self.state_box.addWidget(badge("Đã áp dụng", "ok") if mb.applied
                                 else badge("Chưa áp dụng", "warn"))
        self.state_box.addStretch(1)
        pin = self.secrets.pin(mb.address)
        if mb.has_pin:
            self.pin_value.setText(pin if (self._show_pin and pin) else "Có PIN ••••")
        else:
            self.pin_value.setText("Không đặt PIN")
        self.pin_toggle.setVisible(mb.has_pin and bool(pin))
        self.pin_toggle.setText("Ẩn" if self._show_pin else "Hiện")
        secret = self.secrets.secret(mb.address)
        self.secret_value.setText(secret if (self._show_secret and secret) else mask(secret))
        self.secret_toggle.setVisible(bool(secret))
        self.secret_copy.setVisible(bool(secret))
        self.secret_toggle.setText("Ẩn" if self._show_secret else "Hiện")
        regs = self.storage.registrations(mb.address)
        if regs:
            parts = []
            for r in regs:
                via = ""
                if r["account"] and r["account"] != mb.address:
                    via = f", qua {mask(r['account'])}" if r["via_secret"] else f", qua {r['account']}"
                parts.append(f"<b>{html_mod.escape(r['service'])}</b> "
                             f"({fmt_dt(r['created_at'])}{html_mod.escape(via)}, "
                             f"{KIND_LABEL.get(r['kind'], r['kind'])})")
            self.reg_value.setText("Đã đăng ký: " + "; ".join(parts))
            self.reg_value.setStyleSheet("color:#1b7a3d;")
            self.reg_btn.setText("Chuyển về Chưa dùng")
        else:
            self.reg_value.setText("Chưa dùng để đăng ký")
            self.reg_value.setStyleSheet("")
            self.reg_btn.setText("Đánh dấu đã đăng ký…")

    def _toggle_pin(self) -> None:
        self._show_pin = not self._show_pin
        self._render_detail()

    def _toggle_secret(self) -> None:
        self._show_secret = not self._show_secret
        self._render_detail()

    def _copy_secret(self) -> None:
        if self.current:
            s = self.secrets.secret(self.current.address)
            if s:
                QGuiApplication.clipboard().setText(s)
                self.statusBar().showMessage("Đã sao chép địa chỉ bí mật", 3000)

    def copy(self, text: str) -> None:
        QGuiApplication.clipboard().setText(text)
        self.statusBar().showMessage(f"Đã sao chép {text}", 3000)

    # ================================================================ actions
    def add_existing(self) -> None:
        dlg = AddExistingDialog(self.storage, self)
        if dlg.exec() != AddExistingDialog.Accepted:
            return
        addr, secret, pin = dlg.values()
        if pin:
            self.secrets.set(addr, "pin", pin)
        if secret:
            self.secrets.set(addr, "secret", secret)
        mb = self.storage.add_mailbox(addr, has_pin=bool(pin), has_secret=bool(secret),
                                      source="existing")
        self.current = mb
        self.reload_list(verify=addr)

    def create_batch(self) -> None:
        dlg = BatchDialog(self.client, self.storage, self.secrets, self.settings, self)
        dlg.exec()
        if dlg.bridge is not None:
            dlg.bridge.dispose()
        dlg.deleteLater()
        self.reload_list()

    def create_manual(self) -> None:
        ManualCreateDialog(self.storage, self.secrets, 1, self.settings.ttl, self).exec()
        self.reload_list()

    def open_settings(self) -> None:
        dlg = SettingsDialog(self.settings, self.secrets, self, auto_update=self.updater.auto)
        if dlg.exec() != SettingsDialog.Accepted:
            return
        if dlg.auto_update.isChecked() != self.updater.auto:
            self.updater.auto = dlg.auto_update.isChecked()
        self.notifier.start()

    # ================================================================ tray & codes
    def show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def show_codes(self) -> None:
        self.show_from_tray()
        if self._codes_dlg is None:
            self._codes_dlg = CodesDialog(self.storage, self.open_mailbox, self)
            self._codes_dlg.finished.connect(lambda *_: setattr(self, "_codes_dlg", None))
            self._codes_dlg.show()
        else:
            self._codes_dlg.reload()
            self._codes_dlg.raise_()

    def open_mailbox(self, address: str) -> None:
        self.search.clear()
        want = 1 if self.storage.registrations(address) else 0
        if self.tabs.currentIndex() != want:
            self.tabs.setCurrentIndex(want)
        for i in range(self.list.count()):
            mb = self.storage.get_mailbox(self.list.item(i).data(Qt.UserRole))
            if mb and mb.address == address.lower():
                self.list.setCurrentRow(i)
                return

    def _on_new_codes(self, result) -> None:
        if self._codes_dlg is not None:
            self._codes_dlg.reload()
        touched = {e.mailbox for e in result.codes} | {r.mailbox for r in result.registrations}
        if result.registrations or any(e.newly_registered for e in result.codes):
            self.reload_list()  # hộp vừa đăng ký chuyển sang mục Đã đăng ký
        if self.current and self.current.address in touched:
            self.refresh_mails(silent=True)

    def quit_app(self) -> None:
        self._quitting = True
        self.notifier.shutdown()
        QApplication.instance().quit()

    def closeEvent(self, ev) -> None:  # noqa: N802
        tray = self.notifier.tray
        if (not self._quitting and self.settings.tray_on_close and tray.isVisible()):
            ev.ignore()
            self.hide()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                tray.showMessage(APP_NAME, "Ứng dụng vẫn chạy ở khay hệ thống và sẽ báo khi có "
                                 "mã xác nhận mới. Chuột phải biểu tượng → Thoát để tắt hẳn.",
                                 QSystemTrayIcon.Information, 8000)
            return
        self._quitting = True
        self.notifier.shutdown()
        ev.accept()
        QApplication.instance().quit()

    # ================================================================ registrations
    def _on_tab(self, index: int) -> None:
        self.reg_bar.setVisible(index == 1)
        self.reload_list()

    def _refresh_service_filter(self) -> None:
        cur = self.service_filter.currentData()
        services = self.storage.services()
        self.service_filter.blockSignals(True)
        self.service_filter.clear()
        self.service_filter.addItem("Tất cả dịch vụ", None)
        for name in services:
            self.service_filter.addItem(name, name)
        idx = self.service_filter.findData(cur)
        self.service_filter.setCurrentIndex(max(idx, 0))
        self.service_filter.blockSignals(False)

    def _list_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is None:
            return
        mb = self.storage.get_mailbox(item.data(Qt.UserRole))
        if not mb:
            return
        menu = QMenu(self)
        menu.addAction("Sao chép địa chỉ", lambda: self.copy(mb.address))
        menu.addAction("Mở trên web", lambda: QDesktopServices.openUrl(QUrl(web_url(mb.address))))
        menu.addSeparator()
        menu.addAction("Đánh dấu đã đăng ký…", lambda: self.mark_registered(mb.address))
        if self.storage.registrations(mb.address):
            menu.addAction("Chuyển về Chưa dùng", lambda: self.unmark_registered(mb.address))
        menu.exec(self.list.viewport().mapToGlobal(pos))

    def mark_registered(self, address: str) -> None:
        name, ok = QInputDialog.getItem(
            self, "Đánh dấu đã đăng ký", f"{address} đã dùng để đăng ký dịch vụ nào?",
            self.storage.services() or ["Meta", "Facebook", "Google", "TikTok"], 0, True)
        name = (name or "").strip()
        if not ok or not name:
            return
        self.storage.add_registration(mailbox=address, service=name, kind="manual",
                                      source="manual")
        self.statusBar().showMessage(f"Đã chuyển {address} sang Đã đăng ký ({name})", 5000)
        self.open_mailbox(address)
        self.reload_list()

    def unmark_registered(self, address: str) -> None:
        if QMessageBox.question(
                self, "Chuyển về Chưa dùng",
                f"Xoá đánh dấu đã đăng ký của {address} và chuyển về Chưa dùng?") != QMessageBox.Yes:
            return
        self.storage.clear_registrations(address)
        self.statusBar().showMessage(f"Đã chuyển {address} về Chưa dùng", 5000)
        self.open_mailbox(address)
        self.reload_list()

    def _toggle_registered(self) -> None:
        if not self.current:
            return
        if self.storage.registrations(self.current.address):
            self.unmark_registered(self.current.address)
        else:
            self.mark_registered(self.current.address)

    def _shown_addresses(self) -> list[str]:
        return list(getattr(self, "_shown", []))

    def export_txt(self) -> None:
        addrs = self._shown_addresses()
        if not addrs:
            QMessageBox.information(self, "Xuất danh sách", "Danh sách đang trống.")
            return
        name = "da-dang-ky.txt" if self.tabs.currentIndex() == 1 else "chua-dung.txt"
        path, _ = QFileDialog.getSaveFileName(self, "Lưu danh sách", name, "Văn bản (*.txt)")
        if not path:
            return
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(format_list(addrs))
        self.statusBar().showMessage(f"Đã xuất {len(addrs)} hộp thư ra {path}", 8000)

    def copy_list(self) -> None:
        addrs = self._shown_addresses()
        QGuiApplication.clipboard().setText(format_list(addrs))
        self.statusBar().showMessage(f"Đã sao chép {len(addrs)} dòng vào clipboard", 5000)

    def export_registered(self) -> None:
        import csv
        path, _ = QFileDialog.getSaveFileName(self, "Xuất danh sách đã đăng ký",
                                              "da-dang-ky.csv", "CSV (*.csv)")
        if not path:
            return
        shown = set(self._shown_addresses())
        regs = [r for r in self.storage.registrations() if r["mailbox"] in shown] \
            if self.tabs.currentIndex() == 1 else self.storage.registrations()
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["Hộp thư", "Địa chỉ bí mật", "Dịch vụ", "Đăng ký qua", "Nhận biết từ",
                        "Ngày", "Tiêu đề thư"])
            for r in regs:
                w.writerow([r["mailbox"], self.secrets.secret(r["mailbox"]), r["service"],
                            r["account"], KIND_LABEL.get(r["kind"], r["kind"]),
                            r["created_at"], r["subject"]])
        self.statusBar().showMessage(f"Đã xuất {len(regs)} dòng ra {path}", 8000)

    def remove_current(self) -> None:
        mb = self.current
        if not mb:
            return
        if QMessageBox.question(
                self, "Xoá khỏi danh sách",
                f"Xoá {mb.address} khỏi danh sách trên máy?\n"
                "PIN và địa chỉ bí mật đã lưu cũng sẽ bị xoá. Hộp thư trên TempMail.Plus không bị ảnh hưởng."
        ) != QMessageBox.Yes:
            return
        self.secrets.forget(mb.address)
        self.storage.forget_mailbox_mails(mb.address)
        self.storage.delete_mailbox(mb.id)
        self.current = None
        self.mails.setRowCount(0)
        self.viewer.clear()
        self.reload_list()
        self._render_detail()

    def verify(self, address: str) -> None:
        """Đọc lại cài đặt từ TempMail.Plus; chỉ khi đọc được mới đánh dấu Đã áp dụng."""
        mb = self.storage.find(address)
        if not mb:
            return
        pin = self.secrets.pin(address)

        def work():
            box = self.client.get_box(address, epin=pin)
            secret = None
            if not self.secrets.secret(address):
                try:
                    secret = self.client.get_secret_address(address, epin=pin)
                except TempMailError:
                    secret = None
            return box, secret

        def done(res):
            box, secret = res
            if secret:
                self.secrets.set(address, "secret", secret)
            ok = box.ttl_minutes in LIFETIMES
            fields = {"applied": ok, "has_secret": bool(self.secrets.secret(address))}
            if ok:
                fields.update(ttl_minutes=box.ttl_minutes, applied_at=now_iso())
            self.storage.update_mailbox(mb.id, **fields)
            self.statusBar().showMessage(
                f"{address}: " + ("cài đặt đã được lưu trên TempMail.Plus" if ok
                                  else "chưa có cài đặt lưu trên TempMail.Plus"), 5000)
            self.reload_list()

        self.statusBar().showMessage(f"Đang kiểm tra cài đặt {address}…")
        run_async(work, done, lambda e: self._net_error(e, address))

    def change_settings(self) -> None:
        mb = self.current
        if not mb:
            return
        dlg = ApplySettingsDialog(mb.address, mb.ttl_minutes or self.settings.ttl, mb.has_pin, self)
        if dlg.exec() != ApplySettingsDialog.Accepted:
            return
        ttl = dlg.ttl.currentData()
        old_pin = self.secrets.pin(mb.address)
        if dlg.use_pin.isChecked():
            new_pin = dlg.pin.text() or old_pin
            if not new_pin:
                QMessageBox.warning(self, "Thiếu PIN", "Hãy nhập PIN.")
                return
        else:
            new_pin = ""
        address = mb.address

        def work():
            return self.client.apply_settings(address, ttl, pin=new_pin, epin=old_pin)

        def done(ok: bool):
            self.secrets.set(address, "pin", new_pin)
            self.storage.update_mailbox(
                mb.id, ttl_minutes=ttl if ok else mb.ttl_minutes, has_pin=bool(new_pin),
                applied=ok, applied_at=now_iso() if ok else mb.applied_at)
            self.statusBar().showMessage("Đã áp dụng cài đặt" if ok
                                         else "TempMail.Plus chưa xác nhận cài đặt", 5000)
            self.reload_list()

        self.statusBar().showMessage("Đang lưu cài đặt lên TempMail.Plus…")
        run_async(work, done, lambda e: self._net_error(e, address))

    # ================================================================ mails
    def refresh_mails(self, silent: bool = False) -> None:
        mb = self.current
        if not mb:
            return
        self._req += 1
        req = self._req
        address, pin = mb.address, self.secrets.pin(mb.address)

        def done(mails):
            if req != self._req or not self.current or self.current.address != address:
                return
            ids = [m.mail_id for m in mails]
            if ids != self._mail_ids:
                selected = self._selected_mail_id()
                self._mail_ids = ids
                self.mails.setRowCount(len(mails))
                for r, m in enumerate(mails):
                    sender = f"{m.sender_name} <{m.sender}>" if m.sender_name else m.sender
                    for c, val in enumerate((sender, m.subject, m.date)):
                        it = QTableWidgetItem(val)
                        if m.is_new:
                            f = it.font()
                            f.setBold(True)
                            it.setFont(f)
                        self.mails.setItem(r, c, it)
                if selected in ids:
                    self.mails.blockSignals(True)
                    self.mails.selectRow(ids.index(selected))
                    self.mails.blockSignals(False)
            self.storage.update_mailbox(self.current.id, last_active=now_iso())
            self.statusBar().showMessage(
                f"{address}: {len(mails)} thư · cập nhật lúc {datetime.now():%H:%M:%S}")

        if not silent:
            self.statusBar().showMessage(f"Đang tải thư của {address}…")
        run_async(lambda: self.client.list_mails(address, epin=pin), done,
                  None if silent else (lambda e: self._net_error(e, address)))

    def _selected_mail_id(self) -> int | None:
        rows = self.mails.selectionModel().selectedRows() if self.mails.selectionModel() else []
        if rows and rows[0].row() < len(self._mail_ids):
            return self._mail_ids[rows[0].row()]
        return None

    def _open_mail(self) -> None:
        mid = self._selected_mail_id()
        mb = self.current
        if mid is None or not mb:
            return
        address, pin = mb.address, self.secrets.pin(mb.address)

        def done(d):
            if not self.current or self.current.address != address:
                return
            sender = html_mod.escape(d.sender)
            if d.sender_name:
                sender = f"{html_mod.escape(d.sender_name)} &lt;{sender}&gt;"
            head = (f"<p style='color:#51637a'><b>Từ:</b> {sender}"
                    f"<br><b>Đến:</b> {html_mod.escape(d.to or address)}"
                    f"<br><b>Thời gian:</b> {html_mod.escape(d.date)}</p>"
                    f"<h3>{html_mod.escape(d.subject)}</h3><hr>")
            body = d.html or "<pre style='white-space:pre-wrap'>" + html_mod.escape(d.text) + "</pre>"
            if d.attachments:
                names = ", ".join(html_mod.escape(str(a.get("name", "tệp"))) for a in d.attachments
                                  if isinstance(a, dict))
                body += f"<hr><p><b>Tệp đính kèm:</b> {names} (mở trên web để tải)</p>"
            self.viewer.setHtml(head + body)

        run_async(lambda: self.client.get_mail(address, mid, epin=pin), done,
                  lambda e: self._net_error(e, address))

    # ================================================================ errors
    def _net_error(self, exc: Exception, address: str = "") -> None:
        if isinstance(exc, WrongPin):
            msg = f"PIN đã lưu cho {address} không đúng. Dùng 'Đổi thời hạn / PIN' hoặc thêm lại hộp thư."
        elif isinstance(exc, CaptchaRequired):
            msg = ("TempMail.Plus đang yêu cầu xác minh captcha. Hãy mở hộp thư trên web, "
                   "xác minh, rồi thử lại.")
        else:
            msg = str(exc)
        self.statusBar().showMessage(msg, 8000)
        QMessageBox.warning(self, "TempMail.Plus", msg)
