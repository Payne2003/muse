"""Chạy tác vụ mạng ở luồng nền để giao diện không bị treo."""

from __future__ import annotations

import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThread, QThreadPool, Signal

from muse.core.batch import BatchCreator, BatchResult


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(object)


class _Task(QRunnable):
    def __init__(self, fn: Callable[[], Any]):
        super().__init__()
        self.fn = fn
        self.signals = _Signals()

    def run(self) -> None:
        try:
            try:
                result = self.fn()
            except Exception as exc:  # noqa: BLE001 – chuyển lỗi về luồng giao diện
                traceback.print_exc()
                self.signals.failed.emit(exc)
            else:
                self.signals.done.emit(result)
        except RuntimeError:
            pass  # cửa sổ đã đóng trước khi tác vụ nền xong


_alive: set[_Task] = set()


def run_async(fn: Callable[[], Any], on_done: Callable[[Any], None] | None = None,
              on_error: Callable[[Exception], None] | None = None) -> None:
    task = _Task(fn)
    _alive.add(task)

    def _finish(handler, value):
        _alive.discard(task)
        if handler:
            handler(value)

    task.signals.done.connect(lambda r: _finish(on_done, r))
    task.signals.failed.connect(lambda e: _finish(on_error, e))
    QThreadPool.globalInstance().start(task)


class BatchThread(QThread):
    progress = Signal(int, int, object)  # created, requested, Mailbox|None
    log = Signal(str)
    finished_result = Signal(object)     # BatchResult

    def __init__(self, creator: BatchCreator, parent=None):
        super().__init__(parent)
        self.creator = creator

    def run(self) -> None:
        try:
            result = self.creator.run(
                on_progress=lambda c, n, mb: self.progress.emit(c, n, mb),
                on_log=self.log.emit,
            )
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            result = BatchResult(0, self.creator.count, 0, "error", str(exc))
        self.finished_result.emit(result)

    def stop(self) -> None:
        self.creator.stop()
