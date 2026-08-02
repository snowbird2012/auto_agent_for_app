from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow
from ui.theme import APP_STYLE


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("AutoAgent")
    app.setOrganizationName("AutoAgent")
    app.setStyle("Fusion")
    # Explicit registration also works in stripped-down Windows environments
    # where Qt cannot enumerate the system font collection automatically.
    font_id = QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
    families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
    app.setFont(QFont(families[0] if families else "Microsoft YaHei UI", 10))
    app.setStyleSheet(APP_STYLE)
    app.setAttribute(Qt.ApplicationAttribute.AA_DontShowIconsInMenus, False)

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
