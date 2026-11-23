STYLE = """
* { font-family: "Segoe UI", "Inter", sans-serif; font-size: 10pt; }
QMainWindow, QDialog { background: #f5f8fc; }
QWidget#sidebar { background: #ffffff; border-right: 1px solid #dfe7f1; }
QWidget#panel, QFrame#card { background: #ffffff; border: 1px solid #dfe7f1; border-radius: 10px; }
QLabel#title { font-size: 15pt; font-weight: 600; color: #12355b; }
QLabel#h2 { font-size: 11pt; font-weight: 600; color: #12355b; }
QLabel#muted { color: #6b7c93; }
QLabel#badgeOk { background: #e3f6ea; color: #1b7a3d; border-radius: 9px; padding: 2px 10px; font-weight: 600; }
QLabel#badgeWarn { background: #fff4de; color: #9a6400; border-radius: 9px; padding: 2px 10px; font-weight: 600; }
QLabel#badgeOff { background: #eef2f7; color: #51637a; border-radius: 9px; padding: 2px 10px; }
QPushButton { background: #ffffff; border: 1px solid #c9d6e6; border-radius: 7px; padding: 6px 12px; color: #12355b; }
QPushButton:hover { background: #eef5ff; border-color: #8db4e8; }
QPushButton:disabled { color: #a6b3c3; background: #f3f6fa; }
QPushButton#primary { background: #2f6fde; color: white; border: none; font-weight: 600; }
QPushButton#primary:hover { background: #2559b8; }
QPushButton#primary:disabled { background: #a9c2ee; color: #eef3fc; }
QPushButton#danger { color: #b3261e; }
QPushButton#small { padding: 2px 8px; font-size: 9pt; }
QLineEdit, QSpinBox, QComboBox { background: white; border: 1px solid #c9d6e6; border-radius: 7px; padding: 5px 8px; }
QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border-color: #2f6fde; }
QListWidget { background: transparent; border: none; outline: none; }
QListWidget::item { border-radius: 8px; margin: 2px 4px; }
QListWidget::item:selected { background: #e3eefc; }
QListWidget::item:hover { background: #f0f5fc; }
QTableWidget { background: white; border: 1px solid #dfe7f1; border-radius: 8px; gridline-color: transparent;
               selection-background-color: #e3eefc; selection-color: #12355b; }
QHeaderView::section { background: #f5f8fc; border: none; border-bottom: 1px solid #dfe7f1; padding: 6px; color: #51637a; font-weight: 600; }
QTextBrowser { background: white; border: 1px solid #dfe7f1; border-radius: 8px; }
QProgressBar { border: 1px solid #c9d6e6; border-radius: 7px; background: white; text-align: center; height: 20px; }
QProgressBar::chunk { background: #2f6fde; border-radius: 6px; }
QStatusBar { background: #ffffff; border-top: 1px solid #dfe7f1; color: #51637a; }
"""
