"""Application-wide visual theme."""

APP_STYLE = r"""
QWidget {
    color: #dce7f7;
    font-family: "Microsoft YaHei UI";
    font-size: 13px;
}
QMainWindow, QWidget#AppRoot { background: #07101f; }
QDialog, QMessageBox {
    background: #0d192b;
}
QDialog QLabel, QMessageBox QLabel {
    color: #e7eef9;
    background: transparent;
}
QMessageBox QLabel#qt_msgbox_label {
    color: #edf4ff;
    min-width: 300px;
    padding: 8px 4px;
}
QMessageBox QLabel#qt_msgboxex_icon_label {
    min-width: 48px;
    padding: 4px;
}
QDialogButtonBox QPushButton, QMessageBox QPushButton {
    min-width: 82px;
    color: #eef5ff;
    background: #1b2d47;
    border: 1px solid #365172;
}
QDialogButtonBox QPushButton:hover, QMessageBox QPushButton:hover {
    background: #254262;
    border-color: #5790d5;
}
QWidget#Page, QScrollArea, QScrollArea > QWidget > QWidget { background: #07101f; }
QFrame#Sidebar { background: #0a1425; border-right: 1px solid #1c2b42; }
QFrame#Topbar { background: #081222; border-bottom: 1px solid #1c2b42; }
QFrame#Card, QFrame#Panel {
    background: #0d192b;
    border: 1px solid #1c2d45;
    border-radius: 12px;
}
QFrame#DeviceCard:hover, QFrame#ContactCard:hover { border-color: #3b82f6; }
QLabel#Brand { color: #f4f8ff; font-size: 20px; font-weight: 700; }
QLabel#BrandMark {
    background: #2563eb; color: white; border-radius: 9px;
    font-size: 18px; font-weight: 800;
}
QLabel#PageTitle { color: #f6f9ff; font-size: 24px; font-weight: 700; }
QLabel#SectionTitle { color: #f2f6fc; font-size: 16px; font-weight: 650; }
QLabel#Metric { color: #f8fbff; font-size: 28px; font-weight: 750; }
QLabel#Muted, QLabel#Caption { color: #8494ab; }
QLabel#Small { color: #8494ab; font-size: 11px; }
QLabel#Success { color: #40d7a0; }
QLabel#Warning { color: #f4b95f; }
QLabel#Danger { color: #fb7185; }
QLabel#PillGreen {
    color: #54e0ac; background: #11382f; border-radius: 8px;
    padding: 3px 8px; font-size: 11px;
}
QLabel#PillBlue {
    color: #7db4ff; background: #142c4f; border-radius: 8px;
    padding: 3px 8px; font-size: 11px;
}
QLabel#PillOrange {
    color: #f5c46f; background: #3a2b16; border-radius: 8px;
    padding: 3px 8px; font-size: 11px;
}
QLabel#PillRed {
    color: #ff8fa0; background: #401e2a; border-radius: 8px;
    padding: 3px 8px; font-size: 11px;
}
QPushButton {
    min-height: 34px; padding: 0 14px; border-radius: 8px;
    border: 1px solid #2a3b55; background: #142239; color: #dce7f7;
}
QPushButton:hover { background: #1a2c47; border-color: #42638e; }
QPushButton:pressed { background: #101c30; }
QPushButton#Primary { background: #2563eb; border-color: #3476f4; color: white; font-weight: 600; }
QPushButton#Primary:hover { background: #3473ee; }
QPushButton#DangerButton { color: #fb7185; border-color: #713247; background: #281722; }
QPushButton#CompactActionButton, QPushButton#CompactDangerButton {
    min-height: 18px; max-height: 22px; padding: 0px 4px; margin: 0px;
    border-radius: 5px; font-size: 11px;
}
QPushButton#CompactDangerButton {
    color: #fb7185; border-color: #713247; background: #281722;
}
QPushButton#NavButton {
    text-align: left; border: 0; background: transparent; color: #91a2b9;
    padding-left: 18px; min-height: 42px; font-size: 14px;
}
QPushButton#NavButton:hover { color: #e7f0ff; background: #111f34; }
QPushButton#NavButton:checked {
    color: white; background: #17345e; border-left: 3px solid #4c91ff;
    padding-left: 15px; font-weight: 600;
}
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTimeEdit {
    background: #091525; border: 1px solid #263a55; border-radius: 8px;
    min-height: 34px; padding: 0 10px; selection-background-color: #2563eb;
}
QTextEdit, QPlainTextEdit { padding: 8px; }
QLineEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTimeEdit:focus {
    border-color: #4b8df8;
}
QComboBox::drop-down { border: 0; width: 28px; }
QComboBox {
    color: #e7eef9;
    background: #091525;
}
QComboBox:hover { border-color: #42638e; }
QComboBox:on { border-color: #4b8df8; }
QComboBox QAbstractItemView {
    color: #dce7f7;
    background: #101e32;
    alternate-background-color: #0d192b;
    border: 1px solid #365172;
    border-radius: 6px;
    outline: 0;
    padding: 5px;
    selection-color: #ffffff;
    selection-background-color: #2563eb;
}
QComboBox QAbstractItemView::item {
    min-height: 30px;
    padding: 4px 9px;
    border-radius: 4px;
}
QComboBox QAbstractItemView::item:hover {
    color: #ffffff;
    background: #1d4f91;
}
QCheckBox { spacing: 8px; color: #c6d3e5; }
QCheckBox::indicator {
    width: 17px; height: 17px; border: 1px solid #49617f;
    background: #091525; border-radius: 4px;
}
QCheckBox::indicator:checked { background: #2563eb; border-color: #5b96f7; }
QTableWidget {
    background: #0d192b; alternate-background-color: #0b1728;
    border: 1px solid #1c2d45; border-radius: 10px; gridline-color: #182940;
    selection-background-color: #17345e;
}
QHeaderView::section {
    background: #101e32; color: #8fa1ba; padding: 10px;
    border: 0; border-bottom: 1px solid #253650; font-weight: 600;
}
QTableWidget::item { padding: 8px; border-bottom: 1px solid #17263a; }
QTableWidget#UserManagementTable::item {
    padding: 2px 6px; border-bottom: 1px solid #17263a;
}
QScrollBar:vertical { background: transparent; width: 9px; margin: 2px; }
QScrollBar::handle:vertical { background: #2a3b55; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QProgressBar {
    border: 0; border-radius: 4px; background: #19283d; height: 8px; text-align: center;
}
QProgressBar::chunk { background: #3b82f6; border-radius: 4px; }
QTabWidget::pane { border: 0; }
QTabBar::tab {
    background: transparent; color: #8494ab; padding: 10px 16px;
    border-bottom: 2px solid transparent;
}
QTabBar::tab:selected { color: #f4f8ff; border-bottom-color: #3b82f6; }
QListWidget {
    background: transparent; border: 0; outline: none;
}
QListWidget::item { padding: 12px; border-bottom: 1px solid #19283d; }
QListWidget::item:selected { background: #17345e; border-radius: 8px; }
QToolTip { background: #182842; color: white; border: 1px solid #38506f; padding: 5px; }
"""
