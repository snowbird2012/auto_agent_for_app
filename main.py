from __future__ import annotations

from datetime import datetime
from pathlib import Path
import ctypes
import sys
import tempfile
import traceback
from types import TracebackType


PROJECT_ROOT = Path(__file__).resolve().parent
_reporting_exception = False


def _format_exception(
    error_type: type[BaseException],
    error: BaseException,
    error_traceback: TracebackType | None,
) -> str:
    return "".join(traceback.format_exception(error_type, error, error_traceback))


def _print_to_console(message: str) -> None:
    """Best-effort console output; pythonw.exe may not provide stderr."""
    try:
        if sys.stderr is not None:
            sys.stderr.write(message)
            sys.stderr.flush()
    except Exception:
        pass


def _write_error_log(details: str) -> Path | None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    candidates = [
        PROJECT_ROOT / "data" / "logs",
        Path(tempfile.gettempdir()) / "AutoAgent" / "logs",
    ]
    for directory in candidates:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"startup_error_{timestamp}.log"
            path.write_text(details, encoding="utf-8")
            return path
        except OSError:
            continue
    return None


def _show_native_error(message: str) -> None:
    if sys.platform == "win32":
        try:
            ctypes.windll.user32.MessageBoxW(
                None,
                message,
                "AutoAgent 启动失败",
                0x00000010 | 0x00000000,
            )
            return
        except Exception:
            pass
    _print_to_console(f"{message}\n")


def _show_qt_error(message: str, details: str) -> bool:
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance()
        owns_app = app is None
        if owns_app:
            app = QApplication([])
        dialog = QMessageBox()
        dialog.setIcon(QMessageBox.Icon.Critical)
        dialog.setWindowTitle("AutoAgent 启动失败")
        dialog.setText("AutoAgent 无法启动")
        dialog.setInformativeText(message)
        dialog.setDetailedText(details)
        dialog.setStandardButtons(QMessageBox.StandardButton.Ok)
        dialog.exec()
        if owns_app:
            app.quit()
        return True
    except Exception:
        return False


def report_unhandled_exception(
    error_type: type[BaseException],
    error: BaseException,
    error_traceback: TracebackType | None,
) -> None:
    global _reporting_exception
    if issubclass(error_type, KeyboardInterrupt):
        sys.__excepthook__(error_type, error, error_traceback)
        return
    if _reporting_exception:
        sys.__excepthook__(error_type, error, error_traceback)
        return
    _reporting_exception = True
    try:
        details = _format_exception(error_type, error, error_traceback)
        # Keep console diagnostics when the application was started manually.
        _print_to_console(details)
        log_path = _write_error_log(details)
        summary = f"{error_type.__name__}: {error}"
        if log_path is not None:
            summary += f"\n\n完整错误日志：\n{log_path}"
        else:
            summary += "\n\n错误日志写入失败，请检查项目目录权限。"
        summary += "\n\n请将错误日志提供给开发人员。"
        if not _show_qt_error(summary, details):
            _show_native_error(summary)
    finally:
        _reporting_exception = False


def main() -> int:
    # Imports are deliberately inside main so missing/broken GUI dependencies
    # are handled by the outer startup exception reporter.
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication

    from ui.main_window import MainWindow
    from ui.theme import APP_STYLE

    app = QApplication(sys.argv)
    app.setApplicationName("AutoAgent")
    app.setOrganizationName("AutoAgent")
    app.setStyle("Fusion")
    font_id = QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
    families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
    app.setFont(QFont(families[0] if families else "Microsoft YaHei UI", 10))
    app.setStyleSheet(APP_STYLE)
    app.setAttribute(Qt.ApplicationAttribute.AA_DontShowIconsInMenus, False)

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.excepthook = report_unhandled_exception
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:
        error_type, error, error_traceback = sys.exc_info()
        assert error_type is not None and error is not None
        report_unhandled_exception(error_type, error, error_traceback)
        raise SystemExit(1)
