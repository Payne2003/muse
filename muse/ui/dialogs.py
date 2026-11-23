from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QVBoxLayout,
    QWidget,
)

from muse.core.api import DOMAINS, LIFETIMES, TempMailClient, is_valid_address, web_url
from muse.core.batch import (STATUS_CAPTCHA, STATUS_DONE, STATUS_ERROR, STATUS_STOPPED,
                             BatchCreator, BatchResult)
from muse.core.secrets import SecretStore
from muse.core.storage import Storage

from .workers import BatchThread


def lifetime_combo(selected: int) -> QComboBox:
    cb = QComboBox()
    for mins, label in LIFETIMES.items():
        cb.addItem(label, mins)
    idx = cb.findData(selected)
    cb.setCurrentIndex(idx if idx >= 0 else cb.count() - 1)
    return cb


def domain_combo(selected: str = "") -> QComboBox:
    cb = QComboBox()
    cb.addItem("Ngẫu nhiên", "")
    for d in DOMAINS:
        cb.addItem("@" + d, d)
    idx = cb.findData(selected)
    cb.setCurrentIndex(max(idx, 0))
    return cb


def secret_edit(placeholder: str) -> QLineEdit:
    e = QLineEdit()
    e.setEchoMode(QLineEdit.Password)
    e.setPlaceholderText(placeholder)
    act = e.addAction(e.style().standardIcon(e.style().StandardPixmap.SP_FileDialogContentsView),
                      QLineEdit.TrailingPosition)
    act.setToolTip("Hiện/ẩn")
    act.triggered.connect(lambda: e.setEchoMode(
        QLineEdit.Normal if e.echoMode() == QLineEdit.Password else QLineEdit.Password))
    return e


class AppSettings:
    """Cài đặt mặc định – giống hộp thoại Settings trên TempMail.Plus."""

    def __init__(self, storage: Storage):
        self.storage = storage

    @property
    def ttl(self) -> int:
        try:
            v = int(self.storage.get_setting("default_ttl", "10080"))
        except ValueError:
            v = 10080
        return v if v in LIFETIMES else 10080

    @ttl.setter
    def ttl(self, v: int) -> None:
        self.storage.set_setting("default_ttl", str(int(v)))

    @property
    def pin_enabled(self) -> bool:
        return self.storage.get_setting("pin_enabled", "0") == "1"

    @pin_enabled.setter
    def pin_enabled(self, v: bool) -> None:
        self.storage.set_setting("pin_enabled", "1" if v else "0")

    @property
    def domain(self) -> str:
        return self.storage.get_setting("default_domain", "")

    @domain.setter
    def domain(self, v: str) -> None:
        self.storage.set_setting("default_domain", v)

    @property
    def use_browser(self) -> bool:
        return self.storage.get_setting("use_browser", "1") == "1"

    @use_browser.setter
    def use_browser(self, v: bool) -> None:
        self.storage.set_setting("use_browser", "1" if v else "0")

    @property
    def delay(self) -> float:
        try:
            return max(0.0, float(self.storage.get_setting("batch_delay", "1.5")))
        except ValueError:
            return 1.5

    @delay.setter
    def delay(self, v: float) -> None:
        self.storage.set_setting("batch_delay", str(v))


# --------------------------------------------------------------------------- settings
class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, secrets: SecretStore, parent=None,
                 auto_update: bool = True):
        super().__init__(parent)
        self.setWindowTitle("Cài đặt")
        self.settings = settings
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.ttl = lifetime_combo(settings.ttl)
        self.pin = QCheckBox("Đặt PIN cho hộp thư mới")
        self.pin.setChecked(settings.pin_enabled)
        self.domain = domain_combo(settings.domain)
        self.delay = QSpinBox()
        self.delay.setRange(0, 60)
        self.delay.setSuffix(" giây")
        self.delay.setValue(int(round(settings.delay)))
        form.addRow("Thời hạn hộp thư mặc định", self.ttl)
        form.addRow("", self.pin)
        form.addRow("Tên miền mặc định", self.domain)
        form.addRow("Nghỉ giữa 2 lần tạo", self.delay)
        self.use_browser = QCheckBox("Tạo lô qua trình duyệt tích hợp (hiện captcha để tự tích)")
        self.use_browser.setChecked(settings.use_browser)
        form.addRow("Captcha", self.use_browser)
        self.auto_update = QCheckBox("Tự động kiểm tra và cài bản cập nhật")
        self.auto_update.setChecked(auto_update)
        form.addRow("Cập nhật", self.auto_update)
        lay.addLayout(form)
        store = ("Windows Credential Manager" if secrets.persistent
                 else "bộ nhớ tạm (Credential Manager không khả dụng)")
        note = QLabel(f"PIN và địa chỉ bí mật được lưu trong {store}, không lưu trong cơ sở dữ liệu.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        lay.addWidget(note)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setText("Lưu")
        bb.button(QDialogButtonBox.Cancel).setText("Huỷ")
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _save(self) -> None:
        self.settings.ttl = self.ttl.currentData()
        self.settings.pin_enabled = self.pin.isChecked()
        self.settings.domain = self.domain.currentData()
        self.settings.delay = float(self.delay.value())
        self.settings.use_browser = self.use_browser.isChecked()
        self.accept()


# --------------------------------------------------------------------------- add existing
class AddExistingDialog(QDialog):
    """Thêm hộp thư đang có: địa chỉ nhận thư + địa chỉ bí mật của đúng hộp đó."""

    def __init__(self, storage: Storage, parent=None, title: str = "Thêm hộp thư đang có",
                 intro: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(460)
        self.storage = storage
        lay = QVBoxLayout(self)
        if intro:
            lb = QLabel(intro)
            lb.setWordWrap(True)
            lb.setObjectName("muted")
            lay.addWidget(lb)
        form = QFormLayout()
        self.address = QLineEdit()
        self.address.setPlaceholderText("vd: abc123@mailto.plus (địa chỉ hiển thị ở trang chính)")
        self.secret = secret_edit("Địa chỉ bí mật trong Settings (không bắt buộc)")
        self.pin = secret_edit("PIN nếu hộp thư đã đặt PIN (không bắt buộc)")
        form.addRow("Địa chỉ nhận thư", self.address)
        form.addRow("Địa chỉ bí mật", self.secret)
        form.addRow("PIN", self.pin)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Thêm")
        bb.button(QDialogButtonBox.Cancel).setText("Huỷ")
        bb.accepted.connect(self._check)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def values(self) -> tuple[str, str, str]:
        return (self.address.text().strip().lower(), self.secret.text().strip().lower(),
                self.pin.text())

    def _check(self) -> None:
        addr, secret, _ = self.values()
        if not is_valid_address(addr):
            QMessageBox.warning(self, "Địa chỉ không hợp lệ", "Hãy nhập đúng địa chỉ dạng ten@tenmien.")
            return
        if secret and not is_valid_address(secret):
            QMessageBox.warning(self, "Địa chỉ bí mật không hợp lệ",
                                "Địa chỉ bí mật phải có dạng ten@tenmien.")
            return
        if secret and secret == addr:
            QMessageBox.warning(self, "Trùng địa chỉ",
                                "Địa chỉ bí mật phải khác địa chỉ nhận thư.")
            return
        if self.storage.exists(addr):
            QMessageBox.information(self, "Đã có", "Hộp thư này đã có trong danh sách.")
            return
        self.accept()


# --------------------------------------------------------------------------- batch
class BatchDialog(QDialog):
    """Tạo một lô: nhập số lượng + chọn thời hạn, hiển thị tiến độ thật (vd 12/20)."""

    def __init__(self, client: TempMailClient, storage: Storage, secrets: SecretStore,
                 settings: AppSettings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Tạo một lô hộp thư")
        self.setMinimumWidth(520)
        self.client, self.storage, self.secrets, self.settings = client, storage, secrets, settings
        self.thread: BatchThread | None = None
        self.result: BatchResult | None = None
        self.created_any = False

        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.count = QSpinBox()
        self.count.setRange(1, 500)
        self.count.setValue(20)
        self.ttl = lifetime_combo(settings.ttl)
        self.domain = domain_combo(settings.domain)
        self.use_pin = QCheckBox("Đặt PIN")
        self.use_pin.setChecked(settings.pin_enabled)
        self.pin = secret_edit("PIN dùng chung cho cả lô")
        self.pin.setEnabled(self.use_pin.isChecked())
        self.use_pin.toggled.connect(self.pin.setEnabled)
        pin_row = QHBoxLayout()
        pin_row.addWidget(self.use_pin)
        pin_row.addWidget(self.pin, 1)
        form.addRow("Số lượng", self.count)
        form.addRow("Thời hạn", self.ttl)
        form.addRow("Tên miền", self.domain)
        form.addRow("PIN", pin_row)
        lay.addLayout(form)

        self.progress = QProgressBar()
        self.progress.setFormat("%v/%m")
        self.progress.setValue(0)
        self.status = QLabel("Chưa bắt đầu.")
        self.status.setWordWrap(True)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)
        self.log.setFixedHeight(130)
        lay.addWidget(self.progress)
        lay.addWidget(self.status)
        lay.addWidget(self.log)

        # Trình duyệt tích hợp: gửi yêu cầu như trên web và hiện ô captcha khi trang đòi
        self.bridge = None
        self.cap_panel = QWidget()
        cap_lay = QVBoxLayout(self.cap_panel)
        cap_lay.setContentsMargins(0, 0, 0, 0)
        cap_head = QHBoxLayout()
        cap_lbl = QLabel("<b>Cần xác minh captcha.</b> Tích vào ô bên dưới, lô sẽ tự chạy tiếp.")
        cap_lbl.setWordWrap(True)
        cap_skip = QPushButton("Bỏ qua (dừng lô)")
        cap_skip.clicked.connect(self._skip_captcha)
        cap_head.addWidget(cap_lbl, 1)
        cap_head.addWidget(cap_skip)
        cap_lay.addLayout(cap_head)
        try:
            from .webclient import WEB_AVAILABLE, WebBridge, WebSession
            if WEB_AVAILABLE and settings.use_browser:
                self.bridge = WebBridge(self)
                cap_lay.addWidget(self.bridge.view, 1)
                self.bridge.captcha_shown.connect(self._captcha_shown)
                self.bridge.captcha_hidden.connect(self._captcha_hidden)
                self.client = TempMailClient(session=WebSession(self.bridge))
        except Exception as exc:  # noqa: BLE001 – không có trình duyệt: dùng kết nối thường
            self.bridge = None
            self.log.appendPlainText(f"Không mở được trình duyệt tích hợp: {exc}")
        self.cap_panel.setVisible(False)
        lay.addWidget(self.cap_panel, 1)

        row = QHBoxLayout()
        self.manual_btn = QPushButton("Tạo thủ công…")
        self.manual_btn.setToolTip("Mở TempMail.Plus để tự tạo từng hộp rồi ghi nhận vào ứng dụng")
        self.manual_btn.setVisible(False)
        self.manual_btn.clicked.connect(self._manual)
        self.start_btn = QPushButton("Bắt đầu")
        self.start_btn.setObjectName("primary")
        self.start_btn.clicked.connect(self._start)
        self.stop_btn = QPushButton("Dừng")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop)
        self.close_btn = QPushButton("Đóng")
        self.close_btn.clicked.connect(self.close)
        row.addWidget(self.manual_btn)
        row.addStretch(1)
        row.addWidget(self.start_btn)
        row.addWidget(self.stop_btn)
        row.addWidget(self.close_btn)
        lay.addLayout(row)

    def _inputs(self, enabled: bool) -> None:
        for w in (self.count, self.ttl, self.domain, self.use_pin):
            w.setEnabled(enabled)
        self.pin.setEnabled(enabled and self.use_pin.isChecked())

    def _start(self) -> None:
        pin = self.pin.text() if self.use_pin.isChecked() else ""
        if self.use_pin.isChecked() and not pin:
            QMessageBox.warning(self, "Thiếu PIN", "Hãy nhập PIN hoặc tắt tuỳ chọn Đặt PIN.")
            return
        creator = BatchCreator(self.client, self.storage, self.secrets,
                               count=self.count.value(), ttl_minutes=self.ttl.currentData(),
                               pin=pin, domain=self.domain.currentData() or None,
                               delay=self.settings.delay)
        if self.bridge is not None:
            creator.solve_captcha = lambda: self.bridge.solve_captcha(lambda: creator.stopped)
        self.progress.setRange(0, self.count.value())
        self.progress.setValue(0)
        self.log.clear()
        self.manual_btn.setVisible(False)
        self.status.setText("Đang tạo…")
        self._inputs(False)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.thread = BatchThread(creator, self)
        self.thread.progress.connect(self._on_progress)
        self.thread.log.connect(self.log.appendPlainText)
        self.thread.finished_result.connect(self._on_done)
        self.thread.start()

    def _on_progress(self, created: int, requested: int, mb) -> None:
        self.progress.setValue(created)
        self.status.setText(f"Đã tạo {created}/{requested} hộp thư")
        if mb is not None:
            self.created_any = True
            parent = self.parent()
            if parent is not None and hasattr(parent, "reload_list"):
                parent.reload_list()

    def _on_done(self, res: BatchResult) -> None:
        self.result = res
        self.thread = None
        self._inputs(True)
        self.start_btn.setEnabled(True)
        self.start_btn.setText("Tạo lô mới")
        self.stop_btn.setEnabled(False)
        msg = {
            STATUS_DONE: f"Hoàn tất: đã tạo {res.created}/{res.requested} hộp thư.",
            STATUS_STOPPED: f"Đã dừng ở {res.created}/{res.requested}. "
                            f"{res.created} hộp đã tạo vẫn có trong danh sách.",
            STATUS_CAPTCHA: f"Dừng ở {res.created}/{res.requested}: chưa xác minh captcha. "
                            "Bấm nút bên dưới để tạo tiếp phần còn lại (sẽ hỏi captcha lại).",
            STATUS_ERROR: f"Dừng ở {res.created}/{res.requested} do lỗi: {res.message}",
        }.get(res.status, res.message)
        self.status.setText(msg)
        remaining = res.requested - res.created
        if res.status != STATUS_DONE and remaining > 0:
            self.count.setValue(remaining)
            self.start_btn.setText(f"Tạo tiếp {remaining} hộp còn lại")
        if res.status in (STATUS_CAPTCHA, STATUS_ERROR) and remaining > 0:
            self.manual_btn.setVisible(True)

    def _captcha_shown(self) -> None:
        self.cap_panel.setVisible(True)
        self.status.setText("Đang chờ bạn xác minh captcha…")
        self.resize(max(self.width(), 560), max(self.height(), 900))
        self.raise_()
        self.activateWindow()

    def _captcha_hidden(self) -> None:
        self.cap_panel.setVisible(False)
        self.adjustSize()
        if self.thread:
            self.status.setText("Đã xác minh, đang tạo tiếp…")

    def _skip_captcha(self) -> None:
        if self.bridge is not None:
            self.bridge.cancel_captcha()

    def _stop(self) -> None:
        if self.thread:
            self.stop_btn.setEnabled(False)
            self.status.setText("Đang dừng… (hộp đang tạo dở sẽ được hoàn tất hoặc bỏ qua)")
            self.thread.stop()
            if self.bridge is not None:
                self.bridge.cancel_captcha()

    def _manual(self) -> None:
        remaining = (self.result.requested - self.result.created) if self.result else self.count.value()
        dlg = ManualCreateDialog(self.storage, self.secrets, remaining,
                                 self.ttl.currentData(), self.parent())
        dlg.exec()

    def closeEvent(self, ev) -> None:  # noqa: N802
        if self.thread and self.thread.isRunning():
            if QMessageBox.question(self, "Đang tạo", "Dừng lô đang chạy và đóng?") != QMessageBox.Yes:
                ev.ignore()
                return
            self.thread.stop()
            if self.bridge is not None:
                self.bridge.cancel_captcha()
            self.thread.wait(15000)
        super().closeEvent(ev)

    def reject(self) -> None:
        self.close()


# --------------------------------------------------------------------------- manual
class ManualCreateDialog(QDialog):
    """Nhánh "chưa xác minh": mở TempMail.Plus để người dùng tạo từng hộp, rồi ghi nhận."""

    def __init__(self, storage: Storage, secrets: SecretStore, target: int, ttl: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Tạo thủ công trên TempMail.Plus")
        self.setMinimumWidth(520)
        self.storage, self.secrets, self.target, self.ttl = storage, secrets, max(1, target), ttl
        self.done_count = 0
        lay = QVBoxLayout(self)
        guide = QLabel(
            "1. Bấm <b>Mở TempMail.Plus</b>, chọn <b>Tên ngẫu nhiên mới</b>.<br>"
            f"2. Vào <b>Settings</b>: chọn <b>{LIFETIMES.get(ttl, '')}</b>, đặt PIN nếu cần, bấm Save.<br>"
            "3. Dán địa chỉ (và địa chỉ bí mật trong Settings) vào đây, bấm <b>Ghi nhận</b>.")
        guide.setWordWrap(True)
        lay.addWidget(guide)
        open_btn = QPushButton("Mở TempMail.Plus")
        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(web_url())))
        lay.addWidget(open_btn, 0, Qt.AlignLeft)
        form = QFormLayout()
        self.address = QLineEdit()
        self.address.setPlaceholderText("địa chỉ vừa tạo")
        self.secret = secret_edit("địa chỉ bí mật (không bắt buộc)")
        self.pin = secret_edit("PIN đã đặt (nếu có)")
        self.applied = QCheckBox("Tôi đã bấm Save trong Settings của trang")
        form.addRow("Địa chỉ", self.address)
        form.addRow("Địa chỉ bí mật", self.secret)
        form.addRow("PIN", self.pin)
        form.addRow("", self.applied)
        lay.addLayout(form)
        self.progress = QProgressBar()
        self.progress.setRange(0, self.target)
        self.progress.setFormat("%v/%m")
        self.progress.setValue(0)
        lay.addWidget(self.progress)
        row = QHBoxLayout()
        rec = QPushButton("Ghi nhận")
        rec.setObjectName("primary")
        rec.clicked.connect(self._record)
        close = QPushButton("Đóng")
        close.clicked.connect(self.accept)
        row.addStretch(1)
        row.addWidget(rec)
        row.addWidget(close)
        lay.addLayout(row)
        note = QLabel("Trạng thái <b>Đã áp dụng</b> chỉ hiện sau khi ứng dụng đọc lại cài đặt "
                      "từ TempMail.Plus (bấm Kiểm tra cài đặt ở màn hình chính).")
        note.setObjectName("muted")
        note.setWordWrap(True)
        lay.addWidget(note)

    def _record(self) -> None:
        addr = self.address.text().strip().lower()
        secret = self.secret.text().strip().lower()
        pin = self.pin.text()
        if not is_valid_address(addr):
            QMessageBox.warning(self, "Địa chỉ không hợp lệ", "Hãy dán đúng địa chỉ dạng ten@tenmien.")
            return
        if secret and (not is_valid_address(secret) or secret == addr):
            QMessageBox.warning(self, "Địa chỉ bí mật không hợp lệ", "Kiểm tra lại địa chỉ bí mật.")
            return
        if self.storage.exists(addr):
            QMessageBox.information(self, "Đã có", "Hộp thư này đã có trong danh sách.")
            return
        if pin:
            self.secrets.set(addr, "pin", pin)
        if secret:
            self.secrets.set(addr, "secret", secret)
        self.storage.add_mailbox(addr, ttl_minutes=self.ttl if self.applied.isChecked() else None,
                                 has_pin=bool(pin), has_secret=bool(secret), applied=False,
                                 source="manual")
        self.done_count += 1
        self.progress.setValue(min(self.done_count, self.target))
        for w in (self.address, self.secret, self.pin):
            w.clear()
        self.applied.setChecked(False)
        parent = self.parent()
        if parent is not None and hasattr(parent, "reload_list"):
            parent.reload_list(verify=addr)
        self.address.setFocus()


# --------------------------------------------------------------------------- apply settings
class ApplySettingsDialog(QDialog):
    def __init__(self, address: str, ttl: int, has_pin: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Cài đặt hộp thư")
        lay = QVBoxLayout(self)
        lb = QLabel(f"<b>{address}</b>")
        lay.addWidget(lb)
        form = QFormLayout()
        self.ttl = lifetime_combo(ttl)
        self.use_pin = QCheckBox("Đặt PIN")
        self.use_pin.setChecked(has_pin)
        self.pin = secret_edit("PIN mới (để trống = giữ PIN hiện tại)" if has_pin else "PIN")
        self.pin.setEnabled(has_pin)
        self.use_pin.toggled.connect(self.pin.setEnabled)
        form.addRow("Thời hạn", self.ttl)
        form.addRow(self.use_pin, self.pin)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Lưu lên TempMail.Plus")
        bb.button(QDialogButtonBox.Cancel).setText("Huỷ")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
