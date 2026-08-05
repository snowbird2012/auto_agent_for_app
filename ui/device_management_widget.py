"""Real ADB-backed device management page."""

from __future__ import annotations

from functools import partial

import cv2
from PySide6.QtCore import QSize, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from devices import ADBClient, ADBError, AndroidDevice
from ui.widgets import SectionHeader, card_layout, label


def device_setup_status(
    device: AndroidDevice, initializing: bool = False
) -> tuple[str, str, str, bool]:
    """Return headline, instruction, color and whether initialization is clickable."""
    if initializing:
        return (
            "正在初始化",
            "正在安装并检查 uiautomator2，请保持手机解锁和 USB 连接",
            "#7db4ff",
            False,
        )
    if device.state == "unauthorized":
        return (
            "USB调试未授权",
            "USB 调试已开启，请解锁手机并点击“允许 USB 调试”",
            "#f5c46f",
            False,
        )
    if device.state == "offline":
        return (
            "USB调试未授权",
            "设备当前离线，请重新连接 USB，必要时重启 ADB",
            "#fb7185",
            False,
        )
    if device.state == "no permissions":
        return (
            "USB调试未授权",
            "请检查手机驱动和当前 Windows 用户权限",
            "#fb7185",
            False,
        )
    if device.state != "device":
        return (
            "USB调试未授权",
            "请开启 USB 调试并重新连接手机",
            "#fb7185",
            False,
        )
    if device.uiautomator2_initialized is True:
        return "已就绪", "USB 调试已授权 · uiautomator2 运行正常", "#54e0ac", False
    return (
        "未初始化",
        "USB 调试已授权 · 点击“未初始化”安装 uiautomator2",
        "#fb7185",
        True,
    )


class DeviceScanWorker(QThread):
    succeeded = Signal(list)
    failed = Signal(str)

    def __init__(self, client: ADBClient) -> None:
        super().__init__()
        self.client = client

    def run(self) -> None:
        try:
            self.succeeded.emit(self.client.list_devices(include_details=True))
        except Exception as error:
            self.failed.emit(str(error))


class ScreenshotWorker(QThread):
    succeeded = Signal(str, object)
    failed = Signal(str, str)

    def __init__(self, client: ADBClient, serial: str) -> None:
        super().__init__()
        self.client = client
        self.serial = serial

    def run(self) -> None:
        try:
            self.succeeded.emit(self.serial, self.client.screenshot(self.serial))
        except Exception as error:
            self.failed.emit(self.serial, str(error))


class DeviceActionWorker(QThread):
    succeeded = Signal(str, str)
    failed = Signal(str, str)

    def __init__(self, client: ADBClient, serial: str, action: str) -> None:
        super().__init__()
        self.client = client
        self.serial = serial
        self.action = action

    def run(self) -> None:
        try:
            if self.action == "home":
                self.client.press_home(self.serial)
                message = "已返回手机桌面"
            elif self.action == "start_tiktok":
                self.client.start_app(self.serial)
                message = "已启动 TikTok"
            elif self.action == "stop_tiktok":
                self.client.force_stop_app(self.serial)
                message = "已停止 TikTok"
            elif self.action == "init_uiautomator2":
                self.client.initialize_uiautomator2(self.serial)
                message = "uiautomator2 初始化完成"
            else:
                raise ADBError(f"未知设备操作：{self.action}")
            self.succeeded.emit(self.serial, message)
        except Exception as error:
            self.failed.emit(self.serial, str(error))


class DeviceManagementWidget(QWidget):
    devices_updated = Signal(list)

    def __init__(self, client: ADBClient) -> None:
        super().__init__()
        self.client = client
        self.devices: dict[str, AndroidDevice] = {}
        self.scan_worker: DeviceScanWorker | None = None
        self.screenshot_worker: ScreenshotWorker | None = None
        self.action_worker: DeviceActionWorker | None = None
        self.selected_serial: str | None = None
        self._build_ui()
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(15_000)
        self.refresh_timer.timeout.connect(self.scan_devices)
        self.refresh_timer.start()
        QTimer.singleShot(0, self.scan_devices)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 28)
        layout.setSpacing(18)

        header = QHBoxLayout()
        header.addWidget(SectionHeader("设备管理", "通过 ADB 发现、监控并操作已连接的 Android 设备"))
        header.addStretch()
        adb_status = self.client.adb_path or "未找到，请到系统设置中配置"
        self.scan_status = label(f"ADB：{adb_status}", "Small")
        header.addWidget(self.scan_status)
        self.scan_button = QPushButton("扫描 USB 设备")
        self.scan_button.setObjectName("Primary")
        self.scan_button.clicked.connect(self.scan_devices)
        header.addWidget(self.scan_button)
        layout.addLayout(header)

        summary, summary_layout = card_layout()
        summary_row = QHBoxLayout()
        self.summary_values: dict[str, QLabel] = {}
        for key, title, color in [
            ("total", "发现设备", "#7db4ff"),
            ("online", "已连接", "#54e0ac"),
            ("unauthorized", "未授权", "#f5c46f"),
            ("offline", "离线", "#ff8fa0"),
        ]:
            block = QVBoxLayout()
            value = label("0", "Metric")
            value.setStyleSheet(f"color:{color}; font-size:25px; font-weight:700;")
            self.summary_values[key] = value
            block.addWidget(value)
            block.addWidget(label(title, "Muted"))
            summary_row.addLayout(block)
            summary_row.addStretch()
        summary_layout.addLayout(summary_row)
        layout.addWidget(summary)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["设备", "ADB 序列号", "Android", "分辨率", "电量", "前台应用", "状态"])
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        device_header = self.table.horizontalHeader()
        device_header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        device_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        device_header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        device_header.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        device_header.resizeSection(6, 130)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.cellClicked.connect(self._status_cell_clicked)
        self.table.setMinimumHeight(220)
        layout.addWidget(self.table)

        detail, detail_layout = card_layout()
        detail_header = QHBoxLayout()
        self.detail_title = label("设备详情", "SectionTitle")
        detail_header.addWidget(self.detail_title)
        detail_header.addStretch()
        self.detail_state = label("未选择设备", "PillBlue")
        detail_header.addWidget(self.detail_state)
        detail_layout.addLayout(detail_header)
        body = QHBoxLayout()
        self.preview = QLabel("选择一台已连接设备\n以读取实时截图")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setFixedSize(220, 390)
        self.preview.setStyleSheet("background:#050a12; border:4px solid #24334a; border-radius:20px; color:#60738d;")
        body.addWidget(self.preview)

        info_area = QVBoxLayout()
        info_grid = QGridLayout()
        self.detail_values: dict[str, QLabel] = {}
        fields = [
            ("serial", "ADB 序列号"), ("connection", "连接方式"),
            ("android", "Android 版本"), ("sdk", "SDK 版本"),
            ("resolution", "屏幕分辨率"), ("battery", "电池状态"),
            ("product", "产品代号"), ("foreground", "前台应用"),
        ]
        for index, (key, title) in enumerate(fields):
            row, column = divmod(index, 2)
            box, box_layout = card_layout(10)
            box_layout.addWidget(label(title, "Small"))
            value = label("—")
            value.setWordWrap(True)
            self.detail_values[key] = value
            box_layout.addWidget(value)
            info_grid.addWidget(box, row, column)
        info_area.addLayout(info_grid)
        self.action_status = label("等待设备选择", "Muted")
        info_area.addWidget(self.action_status)
        info_area.addStretch()
        actions = QHBoxLayout()
        self.screenshot_button = QPushButton("刷新截图")
        self.screenshot_button.clicked.connect(self.refresh_screenshot)
        self.home_button = QPushButton("返回桌面")
        self.home_button.clicked.connect(partial(self.run_action, "home"))
        self.start_button = QPushButton("启动 TikTok")
        self.start_button.setObjectName("Primary")
        self.start_button.clicked.connect(partial(self.run_action, "start_tiktok"))
        self.stop_button = QPushButton("停止 TikTok")
        self.stop_button.setObjectName("DangerButton")
        self.stop_button.clicked.connect(partial(self.run_action, "stop_tiktok"))
        for button in (self.screenshot_button, self.home_button, self.start_button, self.stop_button):
            button.setEnabled(False)
            actions.addWidget(button)
        actions.addStretch()
        info_area.addLayout(actions)
        body.addLayout(info_area, 1)
        detail_layout.addLayout(body)
        layout.addWidget(detail)
        layout.addStretch()

    def scan_devices(self) -> None:
        if self.client.adb_path is None:
            self.scan_status.setText("ADB：未找到，请到系统设置中配置")
            self.devices_updated.emit([])
            return
        if self.scan_worker and self.scan_worker.isRunning():
            return
        if self.action_worker and self.action_worker.action == "init_uiautomator2":
            return
        self.scan_button.setEnabled(False)
        self.scan_button.setText("扫描中…")
        self.scan_status.setText("正在查询 ADB 设备和系统信息…")
        worker = DeviceScanWorker(self.client)
        self.scan_worker = worker
        worker.succeeded.connect(self._scan_succeeded)
        worker.failed.connect(self._scan_failed)
        worker.finished.connect(self._scan_finished)
        worker.start()

    def _scan_succeeded(self, devices: list[AndroidDevice]) -> None:
        previous = self.selected_serial
        self.devices = {device.serial: device for device in devices}
        self._update_summary(devices)
        self._populate_table(devices)
        self.devices_updated.emit(devices)
        self.scan_status.setText(f"最近扫描完成 · {len(devices)} 台设备")
        if previous and previous in self.devices:
            self._select_serial(previous)
        elif devices:
            self.table.selectRow(0)
        else:
            self.selected_serial = None
            self._show_empty_detail("未发现设备，请检查 USB 连接和调试授权")

    def _scan_failed(self, message: str) -> None:
        self.scan_status.setText(f"扫描失败：{message}")
        self.devices_updated.emit([])
        QMessageBox.warning(self, "ADB 扫描失败", message)

    def _scan_finished(self) -> None:
        self.scan_button.setEnabled(True)
        self.scan_button.setText("扫描 USB 设备")
        worker = self.scan_worker
        self.scan_worker = None
        if worker:
            worker.deleteLater()

    def _update_summary(self, devices: list[AndroidDevice]) -> None:
        self.summary_values["total"].setText(str(len(devices)))
        self.summary_values["online"].setText(str(sum(item.state == "device" for item in devices)))
        self.summary_values["unauthorized"].setText(str(sum(item.state == "unauthorized" for item in devices)))
        self.summary_values["offline"].setText(str(sum(item.state not in {"device", "unauthorized"} for item in devices)))

    def _populate_table(self, devices: list[AndroidDevice]) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(len(devices))
        for row, device in enumerate(devices):
            battery = f"{device.battery_level}%" if device.battery_level is not None else "—"
            initializing = bool(
                self.action_worker
                and self.action_worker.action == "init_uiautomator2"
                and self.action_worker.serial == device.serial
            )
            headline, detail, color, clickable = device_setup_status(
                device, initializing
            )
            values = [
                device.display_name, device.serial,
                device.android_version or "—", device.resolution or "—",
                battery, device.foreground_package or "—",
                headline,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, device.serial)
                if column == 6:
                    item.setForeground(QColor(color))
                    item.setToolTip(
                        detail + (f"\n检测信息：{device.uiautomator2_error}" if device.uiautomator2_error else "")
                    )
                    item.setData(Qt.ItemDataRole.UserRole, "initialize" if clickable else "")
                    if clickable:
                        font = item.font()
                        font.setUnderline(True)
                        item.setFont(font)
                self.table.setItem(row, column, item)
            self.table.setRowHeight(row, 46)
        self.table.blockSignals(False)

    def _selection_changed(self) -> None:
        items = self.table.selectedItems()
        if not items:
            return
        serial = self.table.item(items[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        device = self.devices.get(serial)
        if not device:
            return
        self.selected_serial = serial
        self._show_device(device)
        if device.authorized:
            self.refresh_screenshot()

    def _show_device(self, device: AndroidDevice) -> None:
        self.detail_title.setText(f"设备详情 · {device.display_name}")
        initializing = bool(
            self.action_worker
            and self.action_worker.action == "init_uiautomator2"
            and self.action_worker.serial == device.serial
        )
        state, instruction, _color, _clickable = device_setup_status(
            device, initializing
        )
        self.detail_state.setText(state)
        self.detail_state.setObjectName(
            "PillBlue" if initializing else
            ("PillGreen" if device.automation_ready else
            ("PillOrange" if device.state == "unauthorized" else "PillRed")
            )
        )
        self.detail_state.style().unpolish(self.detail_state)
        self.detail_state.style().polish(self.detail_state)
        battery = "—" if device.battery_level is None else f"{device.battery_level}% · {device.battery_status}"
        values = {
            "serial": device.serial,
            "connection": device.connection_type,
            "android": device.android_version or "—",
            "sdk": device.sdk_version or "—",
            "resolution": device.resolution or "—",
            "battery": battery,
            "product": device.product or device.device_name or "—",
            "foreground": device.foreground_package or "—",
        }
        for key, value in values.items():
            self.detail_values[key].setText(value)
        for button in (self.screenshot_button, self.home_button, self.start_button, self.stop_button):
            button.setEnabled(device.authorized and self.action_worker is None)
        diagnostic = device.error or device.uiautomator2_error
        self.action_status.setText(
            instruction + (f"\n检测信息：{diagnostic}" if diagnostic else "")
        )

    def _status_cell_clicked(self, row: int, column: int) -> None:
        if column != 6 or self.action_worker is not None:
            return
        status_item = self.table.item(row, column)
        serial_item = self.table.item(row, 0)
        if (
            not status_item
            or status_item.data(Qt.ItemDataRole.UserRole) != "initialize"
            or not serial_item
        ):
            return
        serial = str(serial_item.data(Qt.ItemDataRole.UserRole) or "")
        device = self.devices.get(serial)
        if not device or not device.authorized or device.uiautomator2_initialized is True:
            return
        answer = QMessageBox.question(
            self,
            "初始化设备",
            f"是否在设备 {device.display_name}（{serial}）上初始化 uiautomator2？\n\n"
            "初始化期间请保持手机解锁和 USB 连接。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._select_serial(serial)
        status_item.setText("正在初始化")
        status_item.setToolTip("正在安装并检查 uiautomator2，请保持手机解锁和 USB 连接")
        status_item.setData(Qt.ItemDataRole.UserRole, "")
        font = status_item.font()
        font.setUnderline(False)
        status_item.setFont(font)
        self.run_action("init_uiautomator2")

    def _show_empty_detail(self, message: str) -> None:
        self.detail_title.setText("设备详情")
        self.detail_state.setText("未连接")
        self.preview.clear()
        self.preview.setText(message)
        for value in self.detail_values.values():
            value.setText("—")
        for button in (self.screenshot_button, self.home_button, self.start_button, self.stop_button):
            button.setEnabled(False)
        self.action_status.setText(message)

    def refresh_screenshot(self) -> None:
        serial = self.selected_serial
        device = self.devices.get(serial or "")
        if not device or not device.authorized or (self.screenshot_worker and self.screenshot_worker.isRunning()):
            return
        self.preview.setText("正在读取设备截图…")
        self.screenshot_button.setEnabled(False)
        worker = ScreenshotWorker(self.client, serial)
        self.screenshot_worker = worker
        worker.succeeded.connect(self._screenshot_succeeded)
        worker.failed.connect(self._screenshot_failed)
        worker.finished.connect(self._screenshot_finished)
        worker.start()

    def _screenshot_succeeded(self, serial: str, image) -> None:
        if serial != self.selected_serial:
            return
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        height, width, channels = rgb.shape
        qt_image = QImage(rgb.data, width, height, channels * width, QImage.Format.Format_RGB888).copy()
        pixmap = QPixmap.fromImage(qt_image).scaled(
            self.preview.size() - QSize(12, 12),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.preview.setPixmap(pixmap)
        self.action_status.setText(f"截图已更新 · {width} × {height}")

    def _screenshot_failed(self, serial: str, message: str) -> None:
        if serial == self.selected_serial:
            self.preview.setText("截图读取失败")
            self.action_status.setText(message)

    def _screenshot_finished(self) -> None:
        self.screenshot_button.setEnabled(bool(self.selected_serial and self.devices.get(self.selected_serial, None) and self.devices[self.selected_serial].authorized))
        worker = self.screenshot_worker
        self.screenshot_worker = None
        if worker:
            worker.deleteLater()

    def run_action(self, action: str) -> None:
        serial = self.selected_serial
        device = self.devices.get(serial or "")
        if not device or not device.authorized or (self.action_worker and self.action_worker.isRunning()):
            return
        for button in (self.screenshot_button, self.home_button, self.start_button, self.stop_button):
            button.setEnabled(False)
        self.action_status.setText("正在执行设备操作…")
        worker = DeviceActionWorker(self.client, serial, action)
        self.action_worker = worker
        if action == "init_uiautomator2":
            self.detail_state.setText("正在初始化")
            self.detail_state.setObjectName("PillBlue")
            self.detail_state.style().unpolish(self.detail_state)
            self.detail_state.style().polish(self.detail_state)
            self.action_status.setText(
                "正在安装并检查 uiautomator2，请保持手机解锁和 USB 连接"
            )
        worker.succeeded.connect(self._action_succeeded)
        worker.failed.connect(self._action_failed)
        worker.finished.connect(self._action_finished)
        worker.start()

    def _action_succeeded(self, serial: str, message: str) -> None:
        if serial == self.selected_serial:
            self.action_status.setText(message)
        QTimer.singleShot(700, self.scan_devices)
        QTimer.singleShot(1000, self.refresh_screenshot)

    def _action_failed(self, serial: str, message: str) -> None:
        if serial == self.selected_serial:
            self.action_status.setText(message)
        QMessageBox.warning(self, "设备操作失败", message)

    def _action_finished(self) -> None:
        worker = self.action_worker
        self.action_worker = None
        device = self.devices.get(self.selected_serial or "")
        for button in (self.screenshot_button, self.home_button, self.start_button, self.stop_button):
            button.setEnabled(bool(device and device.authorized))
        if worker:
            if worker.action == "init_uiautomator2":
                QTimer.singleShot(0, self.scan_devices)
            worker.deleteLater()

    def _select_serial(self, serial: str) -> None:
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == serial:
                self.table.selectRow(row)
                return
