from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    from PySide6.QtWidgets import QApplication

    from muse import APP_NAME
    from muse.core.api import TempMailClient
    from muse.core.secrets import SecretStore
    from muse.core.storage import Storage
    from muse.ui.main_window import MainWindow
    from muse.ui.theme import STYLE

    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("Muse")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)

    storage = Storage()
    storage.mark_interrupted_batches()
    win = MainWindow(storage, SecretStore(), TempMailClient())
    win.show()
    code = app.exec()
    storage.close()
    return code
