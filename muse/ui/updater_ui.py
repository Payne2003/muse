"""Tự cập nhật: kiểm tra GitHub Releases khi mở app và định kỳ, tải bộ cài, cài đè, mở lại."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel, QMessageBox,
                               QProgressDialog, QPushButton, QTextBrowser, QVBoxLayout)

from muse import __version__
from muse.core import updater
from muse.core.updater import UpdateError, UpdateInfo

CHECK_EVERY_MS = 6 * 60 * 60 * 1000  # 6 giờ


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int)


class _Task(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.signals = _Signals()

    def run(self):
        try:
            result = self.fn(self.signals)
        except UpdateError as e:
            self.signals.failed.emit(str(e))
        except Exception as e:  # noqa: BLE001
            self.signals.failed.emit(f"{type(e).__name__}: {e}")
        else:
            self.signals.done.emit(result)


class UpdateDialog(QDialog):
    LATER, SKIP, INSTALL = 0, 1, 2

    def __init__(self, info: UpdateInfo, mode: str, parent=None):
        super().__init__(parent)
        self.choice = self.LATER
        self.setWindowTitle("Có bản cập nhật mới")
        self.resize(540, 420)
        size = f" ({info.setup_size / 1024 / 1024:.0f} MB)" if info.setup_size else ""
        if mode == "installed" and info.setup_url:
            how = ("Ứng dụng sẽ tải bộ cài, kiểm tra SHA-256, cài đè rồi tự mở lại. "
                   "Danh sách hộp thư, PIN và địa chỉ bí mật được giữ nguyên.")
            action = f"Cập nhật ngay{size}"
        else:
            how = ("Bạn đang dùng bản portable hoặc chạy từ mã nguồn, nên trang tải bản mới sẽ "
                   "được mở trong trình duyệt. Dữ liệu được giữ nguyên.")
            action = "Mở trang tải về"
        lay = QVBoxLayout(self)
        title = QLabel(f"Phiên bản {info.version} đã sẵn sàng")
        title.setObjectName("title")
        lay.addWidget(title)
        desc = QLabel(f"Bạn đang dùng phiên bản {__version__}. {how}")
        desc.setWordWrap(True)
        lay.addWidget(desc)
        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setMarkdown(info.notes or "_Không có ghi chú phát hành._")
        lay.addWidget(notes, 1)
        row = QHBoxLayout()
        skip = QPushButton("Bỏ qua phiên bản này")
        skip.clicked.connect(lambda: self._done(self.SKIP))
        later = QPushButton("Để sau")
        later.clicked.connect(lambda: self._done(self.LATER))
        go = QPushButton(action)
        go.setObjectName("primary")
        go.setDefault(True)
        go.clicked.connect(lambda: self._done(self.INSTALL))
        row.addWidget(skip)
        row.addStretch(1)
        row.addWidget(later)
        row.addWidget(go)
        lay.addLayout(row)

    def _done(self, choice: int) -> None:
        self.choice = choice
        self.accept()


class UpdateController(QObject):
    available = Signal(object)

    def __init__(self, storage, parent_widget):
        super().__init__(parent_widget)
        self.storage = storage
        self.parent_widget = parent_widget
        self.pool = QThreadPool.globalInstance()
        self.latest: UpdateInfo | None = None
        self._busy = False
        self._prompting = False
        self._tasks: set[_Task] = set()
        self.timer = QTimer(self)
        self.timer.setInterval(CHECK_EVERY_MS)
        self.timer.timeout.connect(lambda: self.check(silent=True))

    @property
    def auto(self) -> bool:
        return self.storage.get_setting("auto_update", "1") == "1"

    @auto.setter
    def auto(self, value: bool) -> None:
        self.storage.set_setting("auto_update", "1" if value else "0")
        self.start()

    def start(self) -> None:
        """Bật/tắt kiểm tra tự động (khi mở app sau 3 giây và mỗi 6 giờ)."""
        if self.auto:
            QTimer.singleShot(3000, lambda: self.check(silent=True))
            self.timer.start()
        else:
            self.timer.stop()

    def _run(self, task: _Task) -> None:
        self._tasks.add(task)
        task.signals.done.connect(lambda *_: self._tasks.discard(task))
        task.signals.failed.connect(lambda *_: self._tasks.discard(task))
        self.pool.start(task)

    def check(self, silent: bool = True) -> None:
        if self._busy:
            return
        self._busy = True
        url = self.storage.get_setting("update_url") or updater.DEFAULT_UPDATE_URL
        task = _Task(lambda _s: updater.check_for_update(url))
        task.signals.done.connect(lambda info: self._on_checked(info, silent))
        task.signals.failed.connect(lambda msg: self._on_failed(msg, silent))
        self._run(task)

    def _on_checked(self, info: UpdateInfo | None, silent: bool) -> None:
        self._busy = False
        self.latest = info
        if info is None:
            if not silent:
                QMessageBox.information(self.parent_widget, "Cập nhật",
                                        f"Bạn đang dùng phiên bản mới nhất ({__version__}).")
            return
        self.available.emit(info)
        if silent and QApplication.activeModalWidget() is not None:
            return  # đang mở hộp thoại khác (vd đang tạo lô): chỉ hiện nút Cập nhật
        if not silent or self.storage.get_setting("skipped_version") != info.version:
            self.prompt(info)

    def _on_failed(self, msg: str, silent: bool) -> None:
        self._busy = False
        if not silent:
            QMessageBox.warning(self.parent_widget, "Cập nhật",
                                f"Không kiểm tra được bản cập nhật.\n\n{msg}")

    def prompt(self, info: UpdateInfo | None = None) -> None:
        info = info or self.latest
        if info is None or self._prompting:
            return
        self._prompting = True
        try:
            mode = updater.install_mode()
            dlg = UpdateDialog(info, mode, self.parent_widget)
            dlg.exec()
        finally:
            self._prompting = False
        if dlg.choice == UpdateDialog.SKIP:
            self.storage.set_setting("skipped_version", info.version)
        elif dlg.choice == UpdateDialog.INSTALL:
            if mode == "installed" and info.setup_url:
                self._download_and_install(info)
            else:
                QDesktopServices.openUrl(QUrl(info.page_url))

    def _download_and_install(self, info: UpdateInfo) -> None:
        dest = Path(tempfile.gettempdir()) / "MuseMailboxUpdate" / (info.setup_name or "setup.exe")
        dlg = QProgressDialog("Đang tải bản cập nhật…", "Huỷ", 0, 100, self.parent_widget)
        dlg.setWindowTitle(f"Cập nhật lên {info.version}")
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setValue(0)
        cancelled = {"v": False}
        dlg.canceled.connect(lambda: cancelled.__setitem__("v", True))

        def work(signals: _Signals):
            def cb(done, total):
                signals.progress.emit(done, total)
                return not cancelled["v"]
            return updater.download(info.setup_url, dest, info.sha256, cb)

        def on_progress(done, total):
            if total:
                dlg.setMaximum(100)
                dlg.setValue(int(done * 100 / total))
                dlg.setLabelText(f"Đang tải… {done / 1048576:.1f} / {total / 1048576:.1f} MB")
            else:
                dlg.setMaximum(0)

        def on_failed(msg):
            dlg.close()
            if not cancelled["v"]:
                QMessageBox.warning(self.parent_widget, "Cập nhật", msg)

        task = _Task(work)
        task.signals.progress.connect(on_progress)
        task.signals.done.connect(lambda path: (dlg.close(), self._run_installer(Path(path))))
        task.signals.failed.connect(on_failed)
        self._run(task)
        dlg.show()

    def _run_installer(self, setup: Path) -> None:
        if sys.platform != "win32":
            QMessageBox.information(self.parent_widget, "Cập nhật", f"Đã tải bộ cài: {setup}")
            return
        msg = "Ứng dụng sẽ đóng để cài bản mới và tự mở lại sau khi cài xong.\n\nTiếp tục?"
        if QMessageBox.question(self.parent_widget, "Cài đặt bản cập nhật", msg) != QMessageBox.Yes:
            return
        flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                 | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        subprocess.Popen([str(setup), "/SP-", "/SILENT", "/NOCANCEL", "/CLOSEAPPLICATIONS"],
                         creationflags=flags, close_fds=True)
        QApplication.quit()
