"""Main desktop window and prototype pages."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import partial
from zoneinfo import ZoneInfo

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from storage import AutomationJobRepository, ConversationRepository, MessageStrategyRepository
from storage.settings_repository import SettingsRepository
from storage.task_repository import TaskRepository
from storage.user_repository import UserRepository
from devices import ADBClient, AndroidDevice
from ui.ai_settings_widget import AISettingsWidget
from ui.automation_tasks_widget import AutomationTasksWidget
from ui.message_strategy_widget import MessageStrategyWidget
from ui.message_management_widget import MessageManagementWidget
from ui.device_management_widget import DeviceManagementWidget
from ui.proxy_settings_widget import ProxySettingsWidget
from ui.task_center_widget import TaskCenterWidget
from ui.user_management_widget import UserManagementWidget
from ui.widgets import DeviceStatusCard, MetricCard, MiniChart, SectionHeader, card_layout, label
from utils.time_utils import COMMON_TIMEZONES, format_utc_timestamp


NAV_ITEMS = [
    "运营总览",
    "设备管理",
    "用户采集",
    "用户管理",
    "自动化任务",
    "消息策略",
    "消息管理",
    "审核中心",
    "系统设置",
]

NOTIFICATION_SETTINGS_ENABLED = False


class MainWindow(QMainWindow):
    def __init__(self, settings_repository: SettingsRepository | None = None) -> None:
        super().__init__()
        self.settings_repository = settings_repository or SettingsRepository()
        self.task_repository = TaskRepository(self.settings_repository.database_path)
        self.user_repository = UserRepository(self.settings_repository.database_path)
        self.automation_job_repository = AutomationJobRepository(
            self.settings_repository.database_path
        )
        self.message_strategy_repository = MessageStrategyRepository(self.settings_repository.database_path)
        self.conversation_repository = ConversationRepository(self.settings_repository.database_path)
        # Keep the settings UI available even before ADB is installed/configured.
        self.adb_client = ADBClient(
            self.settings_repository.get_adb_path(), allow_missing=True
        )
        self.setWindowTitle("AutoAgent · Android")
        self.resize(1480, 900)
        self.setMinimumSize(1180, 720)
        self.nav_buttons: list[QPushButton] = []
        self._build_ui()
        self._switch_page(0)
        if self.adb_client.adb_path is None:
            QTimer.singleShot(0, self._show_missing_adb_prompt)

    def _show_missing_adb_prompt(self) -> None:
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle("未找到 ADB")
        dialog.setText("程序未检测到 ADB")
        dialog.setInformativeText(
            "请安装 Android SDK Platform-Tools。\n\n"
            "如果已经安装，请进入“系统设置 → 基本设置”，手动指定 "
            "adb.exe 文件或 platform-tools 目录。"
        )
        settings_button = dialog.addButton(
            "打开系统设置", QMessageBox.ButtonRole.ActionRole
        )
        dialog.addButton("稍后处理", QMessageBox.ButtonRole.RejectRole)
        dialog.exec()
        if dialog.clickedButton() is settings_button:
            self._switch_page(NAV_ITEMS.index("系统设置"))

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("AppRoot")
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_sidebar())

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self._build_topbar())

        self.pages = QStackedWidget()
        self.pages.addWidget(self._scroll_page(self._dashboard_page()))
        self.device_page = DeviceManagementWidget(self.adb_client)
        self.device_page.devices_updated.connect(self._update_dashboard_devices)
        self.pages.addWidget(self._scroll_page(self.device_page))
        self.task_page = TaskCenterWidget(
            self.adb_client,
            self.task_repository,
            self.user_repository,
            self.settings_repository,
        )
        self.device_page.devices_updated.connect(self.task_page.update_devices)
        self.pages.addWidget(self._scroll_page(self.task_page))
        self.user_page = UserManagementWidget(
            self.user_repository, self.settings_repository
        )
        self.task_page.users_updated.connect(self.user_page.refresh_users)
        self.pages.addWidget(self._scroll_page(self.user_page))
        self.automation_page = AutomationTasksWidget(
            self.automation_job_repository,
            self.user_repository,
            self.settings_repository,
            self.adb_client,
            self.message_strategy_repository,
            self.conversation_repository,
        )
        self.device_page.devices_updated.connect(self.automation_page.update_devices)
        self.pages.addWidget(self._scroll_page(self.automation_page))
        self.message_strategy_page = MessageStrategyWidget(self.message_strategy_repository,self.settings_repository)
        self.pages.addWidget(self._scroll_page(self.message_strategy_page))
        self.message_page = MessageManagementWidget(self.conversation_repository,self.settings_repository)
        self.automation_page.messages_updated.connect(self.message_page.refresh)
        self.pages.addWidget(self._scroll_page(self.message_page))
        self.pages.addWidget(self._scroll_page(self._review_page()))
        self.pages.addWidget(self._scroll_page(self._settings_page()))
        content_layout.addWidget(self.pages, 1)
        layout.addWidget(content, 1)

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(225)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 18, 12, 16)
        layout.setSpacing(5)

        brand = QHBoxLayout()
        mark = label("A", "BrandMark")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setFixedSize(38, 38)
        brand.addWidget(mark)
        brand.addWidget(label("AutoAgent", "Brand"))
        brand.addStretch()
        layout.addLayout(brand)
        subtitle = label("ANDROID INTELLIGENCE", "Small")
        subtitle.setStyleSheet("letter-spacing:1px; color:#56708f; margin-left:47px;")
        layout.addWidget(subtitle)
        layout.addSpacing(22)

        for index, title in enumerate(NAV_ITEMS):
            button = QPushButton(title)
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.clicked.connect(partial(self._switch_page, index))
            self.nav_buttons.append(button)
            layout.addWidget(button)
        layout.addStretch()

        status, status_layout = card_layout(12)
        status_layout.addWidget(label("系统状态", "Small"))
        online = QHBoxLayout()
        dot = QLabel("●")
        dot.setStyleSheet("color:#34d399;")
        online.addWidget(dot)
        online.addWidget(label("服务运行正常"))
        online.addStretch()
        status_layout.addLayout(online)
        self.sidebar_device_status = label("ADB 尚未扫描", "Small")
        status_layout.addWidget(self.sidebar_device_status)
        layout.addWidget(status)
        return sidebar

    def _build_topbar(self) -> QWidget:
        topbar = QFrame()
        topbar.setObjectName("Topbar")
        topbar.setFixedHeight(68)
        layout = QHBoxLayout(topbar)
        layout.setContentsMargins(24, 0, 24, 0)
        breadcrumb = label("工作台  /  运营总览", "Muted")
        self.breadcrumb = breadcrumb
        layout.addWidget(breadcrumb)
        layout.addStretch()
        search = QLineEdit()
        search.setPlaceholderText("搜索用户、任务或设备...")
        search.setFixedWidth(270)
        layout.addWidget(search)
        self.dashboard_notice = QPushButton("暂无失败任务")
        self.dashboard_notice.clicked.connect(lambda: self._switch_page(2))
        layout.addWidget(self.dashboard_notice)
        avatar = QLabel("管")
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setFixedSize(36, 36)
        avatar.setStyleSheet("background:#29466f; color:#dbeafe; border-radius:18px; font-weight:700;")
        layout.addWidget(avatar)
        return topbar

    def _switch_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        for i, button in enumerate(self.nav_buttons):
            button.setChecked(i == index)
        self.breadcrumb.setText(f"工作台  /  {NAV_ITEMS[index]}")
        if index == 0 and hasattr(self, "dashboard_metrics"):
            self._refresh_dashboard_data()
        elif index == 4 and hasattr(self, "automation_page"):
            self.automation_page.refresh_all()
        elif index == 5 and hasattr(self, "message_strategy_page"):
            self.message_strategy_page.refresh_all()
        elif index == 6 and hasattr(self, "message_page"):
            self.message_page.refresh()

    @staticmethod
    def _scroll_page(content: QWidget) -> QScrollArea:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setWidget(content)
        return area

    @staticmethod
    def _page() -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        page.setObjectName("Page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(26, 24, 26, 28)
        layout.setSpacing(18)
        return page, layout

    def _dashboard_page(self) -> QWidget:
        page, layout = self._page()
        header_row = QHBoxLayout()
        header_row.addWidget(SectionHeader("运营总览", "查看真实设备、任务、房间与用户采集数据"))
        header_row.addStretch()
        refresh = QPushButton("刷新数据")
        refresh.clicked.connect(self._toast_refresh)
        header_row.addWidget(refresh)
        start = QPushButton("+  新建任务")
        start.setObjectName("Primary")
        start.clicked.connect(lambda: self._switch_page(2))
        header_row.addWidget(start)
        layout.addLayout(header_row)

        metrics = QGridLayout()
        metrics.setHorizontalSpacing(14)
        metric_data = [
            ("在线设备", "—", "等待 ADB 扫描", "blue"),
            ("今日发现用户", "0", "临时用户库共 0 位", "green"),
            ("今日采集房间", "0", "视频 0 · 直播 0", "orange"),
            ("执行中任务", "0", "今日完成 0 · 失败 0", "pink"),
        ]
        self.dashboard_metrics: dict[str, MetricCard] = {}
        for index, values in enumerate(metric_data):
            card = MetricCard(*values)
            self.dashboard_metrics[values[0]] = card
            if index == 0:
                self.dashboard_device_metric = card
            metrics.addWidget(card, 0, index)
        layout.addLayout(metrics)

        middle = QHBoxLayout()
        trend, trend_layout = card_layout()
        trend_header = QHBoxLayout()
        trend_header.addWidget(label("近 7 日发现用户", "SectionTitle"))
        trend_header.addStretch()
        trend_header.addWidget(label("● 发现用户", "PillBlue"))
        trend_layout.addLayout(trend_header)
        self.dashboard_chart = MiniChart([0, 0, 0, 0, 0, 0, 0])
        trend_layout.addWidget(self.dashboard_chart)
        days = QHBoxLayout()
        self.dashboard_day_labels: list[QLabel] = []
        for _ in range(7):
            item = label("—", "Small")
            item.setAlignment(Qt.AlignmentFlag.AlignCenter)
            days.addWidget(item)
            self.dashboard_day_labels.append(item)
        trend_layout.addLayout(days)
        middle.addWidget(trend, 3)

        activity, activity_layout = card_layout()
        activity_layout.addWidget(label("实时动态", "SectionTitle"))
        self.dashboard_activity_list = QVBoxLayout()
        self.dashboard_activity_list.setSpacing(10)
        activity_layout.addLayout(self.dashboard_activity_list)
        activity_layout.addStretch()
        middle.addWidget(activity, 2)
        layout.addLayout(middle)

        layout.addWidget(label("设备运行状态", "SectionTitle"))
        self.dashboard_device_grid = QGridLayout()
        self.dashboard_device_grid.setHorizontalSpacing(14)
        self.dashboard_device_grid.addWidget(label("正在读取 ADB 设备…", "Muted"), 0, 0)
        layout.addLayout(self.dashboard_device_grid)
        layout.addStretch()
        self._refresh_dashboard_data()
        return page

    @staticmethod
    def _utc_text(value: datetime) -> str:
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    def _refresh_dashboard_data(self) -> None:
        timezone_name = self.settings_repository.get_timezone()
        target_zone = ZoneInfo(timezone_name)
        now = datetime.now(target_zone)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow_start = today_start + timedelta(days=1)
        week_start = today_start - timedelta(days=6)

        user_data = self.user_repository.dashboard_user_data(
            self._utc_text(week_start), self._utc_text(tomorrow_start)
        )
        daily_counts = [0] * 7
        for value in user_data["first_seen_at"]:
            try:
                source = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
                day_index = (source.astimezone(target_zone).date() - week_start.date()).days
                if 0 <= day_index < 7:
                    daily_counts[day_index] += 1
            except ValueError:
                continue
        self.dashboard_chart.set_values(daily_counts)
        for index, item in enumerate(self.dashboard_day_labels):
            day = week_start + timedelta(days=index)
            item.setText("今天" if index == 6 else day.strftime("%m-%d"))

        today_users = daily_counts[-1]
        yesterday_users = daily_counts[-2]
        user_metric = self.dashboard_metrics["今日发现用户"]
        user_metric.set_value(str(today_users))
        user_metric.set_note(
            f"昨日 {yesterday_users} 位 · 临时用户库共 {user_data['total']} 位"
        )

        task_data = self.task_repository.dashboard_task_data(
            self._utc_text(today_start), self._utc_text(tomorrow_start)
        )
        video_rooms = task_data["rooms_today"].get("video", 0)
        live_rooms = task_data["rooms_today"].get("live", 0)
        room_metric = self.dashboard_metrics["今日采集房间"]
        room_metric.set_value(str(video_rooms + live_rooms))
        room_metric.set_note(f"视频 {video_rooms} · 直播 {live_rooms}")

        running = task_data["statuses"].get("running", 0)
        completed = task_data["finished_today"].get("completed", 0)
        failed_today = task_data["finished_today"].get("failed", 0)
        task_metric = self.dashboard_metrics["执行中任务"]
        task_metric.set_value(str(running))
        task_metric.set_note(f"今日完成 {completed} · 失败 {failed_today}")
        failed_total = task_data["statuses"].get("failed", 0)
        self.dashboard_notice.setText(
            f"●  {failed_total} 个失败任务" if failed_total else "暂无失败任务"
        )

        while self.dashboard_activity_list.count():
            item = self.dashboard_activity_list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        activities = task_data["activities"]
        if not activities:
            self.dashboard_activity_list.addWidget(
                label("暂无任务执行记录", "Muted")
            )
            return
        colors = {"ERROR": "#fb7185", "WARN": "#f59e0b", "INFO": "#3b82f6"}
        for event in activities:
            holder = QWidget()
            row = QHBoxLayout(holder)
            row.setContentsMargins(0, 0, 0, 0)
            dot = QLabel("●")
            dot.setStyleSheet(f"color:{colors.get(event['level'], '#3b82f6')};")
            row.addWidget(dot)
            info = QVBoxLayout()
            message = QLabel(f"{event['task_name']} · {event['message']}")
            message.setWordWrap(True)
            info.addWidget(message)
            info.addWidget(label(
                format_utc_timestamp(event["created_at"], timezone_name), "Small"
            ))
            row.addLayout(info, 1)
            self.dashboard_activity_list.addWidget(holder)

    def _update_dashboard_devices(self, devices: list[AndroidDevice]) -> None:
        while self.dashboard_device_grid.count():
            item = self.dashboard_device_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        online = [device for device in devices if device.authorized]
        self.dashboard_device_metric.set_value(f"{len(online)} / {len(devices)}")
        self.dashboard_device_metric.set_note(
            f"已连接 {len(online)} · 未就绪 {len(devices) - len(online)}"
        )
        self.sidebar_device_status.setText(f"{len(online)} 台设备在线 · ADB 实时数据")
        if not devices:
            self.dashboard_device_grid.addWidget(label("未发现 Android 设备，请检查 USB 连接和调试授权。", "Muted"), 0, 0)
            return
        for index, device in enumerate(devices[:3]):
            status = "已连接" if device.authorized else ("未授权" if device.state == "unauthorized" else "离线")
            system = f"Android {device.android_version or '未知'} · {device.connection_type}"
            foreground = device.foreground_package or ("等待 USB 调试授权" if not device.authorized else "ADB 已连接")
            progress = device.battery_level or 0
            self.dashboard_device_grid.addWidget(
                DeviceStatusCard(device.display_name, system, status, foreground, progress), 0, index
            )

    def _contacts_page(self) -> QWidget:
        page, layout = self._page()
        header = QHBoxLayout()
        header.addWidget(SectionHeader("消息管理", "查看联系人及其完整私信记录"))
        header.addStretch()
        export = QPushButton("导出联系人")
        export.clicked.connect(lambda: self._info("导出", "UI 原型暂不写出文件。"))
        header.addWidget(export)
        layout.addLayout(header)

        tools = QHBoxLayout()
        contact_search = QLineEdit(); contact_search.setPlaceholderText("搜索昵称、用户名或消息内容")
        status = QComboBox(); status.addItems(["全部状态", "自动对话中", "等待回复", "需要人工", "已转化"])
        intent = QComboBox(); intent.addItems(["全部意图", "购买意向", "产品咨询", "合作", "一般交流"])
        tools.addWidget(contact_search, 1); tools.addWidget(status); tools.addWidget(intent)
        layout.addLayout(tools)

        splitter = QSplitter()
        left, left_layout = card_layout(8)
        left.setMinimumWidth(270)
        left_layout.addWidget(label("联系人  186", "SectionTitle"))
        self.contact_list = QListWidget()
        contacts = [
            ("Maya Chen", "@maya_design", "想问一下这款帐篷的价格", "2", "购买意向"),
            ("Oliver Camp", "@oliver.outdoor", "Thanks! I will check it out.", "", "产品咨询"),
            ("林晓", "@linxiaotravel", "适合两个人用吗？", "1", "购买意向"),
            ("Home Lab", "@home.lab", "期待合作细节", "", "合作"),
            ("Nora", "@nora_weekend", "好的，谢谢你的介绍", "", "一般交流"),
        ]
        for name, handle, message, unread, tag in contacts:
            item = QListWidgetItem(f"●  {name}   {unread}\n    {handle} · {tag}\n    {message}")
            item.setData(Qt.ItemDataRole.UserRole, (name, handle, message, tag))
            self.contact_list.addItem(item)
        self.contact_list.setCurrentRow(0)
        self.contact_list.currentItemChanged.connect(self._contact_changed)
        left_layout.addWidget(self.contact_list)
        splitter.addWidget(left)

        center, center_layout = card_layout(14)
        chat_head = QHBoxLayout()
        self.chat_name = label("Maya Chen", "SectionTitle")
        chat_head.addWidget(self.chat_name)
        chat_head.addWidget(label("自动对话中", "PillGreen"))
        chat_head.addStretch()
        takeover = QPushButton("人工接管")
        takeover.clicked.connect(lambda: self._info("人工接管", "该会话将在接入业务逻辑后暂停自动回复。"))
        chat_head.addWidget(takeover)
        center_layout.addLayout(chat_head)
        center_layout.addWidget(label("今天", "Small"), alignment=Qt.AlignmentFlag.AlignCenter)
        self.chat_area = QVBoxLayout()
        self._load_mock_chat("Maya Chen")
        center_layout.addLayout(self.chat_area)
        center_layout.addStretch()
        suggestion = QFrame(); suggestion.setStyleSheet("background:#112540; border:1px solid #234a78; border-radius:9px;")
        sug_layout = QVBoxLayout(suggestion)
        sug_layout.addWidget(label("AI 回复建议", "PillBlue"))
        self.reply_box = QTextEdit("你好！这款双人轻量帐篷目前活动价是 129 美元。如果你告诉我主要使用场景，我也可以帮你确认是否合适。")
        self.reply_box.setMaximumHeight(82)
        sug_layout.addWidget(self.reply_box)
        buttons = QHBoxLayout(); buttons.addWidget(QPushButton("重新生成")); buttons.addStretch()
        send = QPushButton("发送回复"); send.setObjectName("Primary"); send.clicked.connect(self._send_mock_reply)
        buttons.addWidget(send); sug_layout.addLayout(buttons)
        center_layout.addWidget(suggestion)
        splitter.addWidget(center)

        right, right_layout = card_layout(14)
        right.setMinimumWidth(260)
        right_layout.addWidget(label("用户信息", "SectionTitle"))
        avatar = QLabel("MC"); avatar.setAlignment(Qt.AlignmentFlag.AlignCenter); avatar.setFixedSize(58, 58)
        avatar.setStyleSheet("background:#244a77; border-radius:29px; color:#aaccff; font-size:18px; font-weight:700;")
        right_layout.addWidget(avatar, alignment=Qt.AlignmentFlag.AlignCenter)
        self.profile_name = label("Maya Chen", "SectionTitle"); self.profile_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_layout.addWidget(self.profile_name)
        right_layout.addWidget(label("@maya_design", "Muted"), alignment=Qt.AlignmentFlag.AlignCenter)
        right_layout.addSpacing(8)
        for title, value in [("意图", "购买意向"), ("线索评分", "86 / 100"), ("关注状态", "已关注"), ("来源关键词", "露营装备"), ("首次发现", "今天 10:24")]:
            r = QHBoxLayout(); r.addWidget(label(title, "Muted")); r.addStretch(); r.addWidget(label(value)); right_layout.addLayout(r)
        right_layout.addSpacing(8)
        right_layout.addWidget(label("来源留言", "Muted"))
        quote = label("“请问这个帐篷在哪里买？适合两个人吗？”")
        quote.setWordWrap(True); quote.setStyleSheet("background:#091525; padding:10px; border-radius:8px; color:#bdcbe0;")
        right_layout.addWidget(quote)
        right_layout.addWidget(label("标签", "Muted"))
        tags = QHBoxLayout(); tags.addWidget(label("高意向", "PillGreen")); tags.addWidget(label("户外", "PillBlue")); tags.addStretch(); right_layout.addLayout(tags)
        right_layout.addStretch()
        splitter.addWidget(right)
        splitter.setSizes([285, 620, 280])
        layout.addWidget(splitter, 1)
        return page

    def _review_page(self) -> QWidget:
        page, layout = self._page()
        layout.addWidget(SectionHeader("审核中心", "确认高风险操作与低置信度模型建议"))
        tabs = QTabWidget()
        for title, count in [("待审核", 3), ("已通过", 18), ("已拒绝", 4)]:
            tab = QWidget(); tab_layout = QVBoxLayout(tab); tab_layout.setContentsMargins(0, 14, 0, 0)
            if title == "待审核":
                for name, kind, reason, tone in [
                    ("@maya_design", "首条私信", "高意向用户，模型建议发送产品价格与选型帮助。", "green"),
                    ("@angry_customer", "敏感会话", "用户表达不满，建议转交人工处理。", "red"),
                    ("@studio_partners", "合作咨询", "涉及商务合作条款，不允许模型自动承诺。", "orange"),
                ]:
                    item, item_layout = card_layout()
                    top = QHBoxLayout(); top.addWidget(label(name, "SectionTitle")); top.addWidget(label(kind, {"green":"PillGreen","red":"PillRed","orange":"PillOrange"}[tone])); top.addStretch(); item_layout.addLayout(top)
                    item_layout.addWidget(label(reason, "Muted"))
                    actions = QHBoxLayout(); actions.addStretch(); reject = QPushButton("拒绝"); approve = QPushButton("批准"); approve.setObjectName("Primary"); actions.addWidget(reject); actions.addWidget(approve); item_layout.addLayout(actions)
                    tab_layout.addWidget(item)
            else:
                tab_layout.addWidget(label(f"{title}记录共 {count} 条", "Muted"))
                tab_layout.addStretch()
            tabs.addTab(tab, f"{title}  {count}")
        layout.addWidget(tabs)
        return page

    def _settings_page(self) -> QWidget:
        page, layout = self._page()
        layout.addWidget(SectionHeader("系统设置", "配置多个 API 厂家、模型类型、自动化边界和消息推送"))
        tabs = QTabWidget()
        tabs.addTab(self._basic_settings(), "基本设置")
        tabs.addTab(AISettingsWidget(self.settings_repository), "AI 模型服务")
        tabs.addTab(ProxySettingsWidget(self.settings_repository), "网络代理")
        tabs.addTab(self._automation_settings(), "自动化规则")
        if NOTIFICATION_SETTINGS_ENABLED:
            tabs.addTab(self._notification_settings(), "消息推送")
        layout.addWidget(tabs)
        return page

    def _basic_settings(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 16, 0, 0)
        card, content = card_layout()
        content.addWidget(label("区域与时间", "SectionTitle"))
        content.addWidget(label(
            "数据库时间统一按 UTC 保存，界面中的列表时间按此时区显示。",
            "Muted",
        ))
        form = QFormLayout()
        form.setSpacing(13)
        timezone_combo = QComboBox()
        timezone_combo.setEditable(True)
        timezone_combo.addItems(list(COMMON_TIMEZONES))
        timezone_combo.setCurrentText(self.settings_repository.get_timezone())
        form.addRow("显示时区", timezone_combo)

        adb_path = QLineEdit(self.settings_repository.get_adb_path())
        adb_path.setPlaceholderText("留空自动查找；也可填写 adb.exe 或 platform-tools 目录")
        adb_path_row = QHBoxLayout()
        adb_path_row.setContentsMargins(0, 0, 0, 0)
        adb_path_row.addWidget(adb_path, 1)
        browse_adb = QPushButton("浏览")
        adb_path_row.addWidget(browse_adb)
        form.addRow("ADB 路径", adb_path_row)

        def choose_adb_path() -> None:
            selected, _ = QFileDialog.getOpenFileName(
                self,
                "选择 adb 可执行文件",
                adb_path.text().strip(),
                "ADB 可执行文件 (adb.exe adb);;所有文件 (*)",
            )
            if selected:
                adb_path.setText(selected)

        browse_adb.clicked.connect(choose_adb_path)
        content.addLayout(form)
        save = QPushButton("保存基本设置")
        save.setObjectName("Primary")

        def save_basic_settings() -> None:
            try:
                timezone_name = self.settings_repository.save_timezone(
                    timezone_combo.currentText()
                )
            except ValueError as error:
                QMessageBox.warning(self, "时区无效", str(error))
                return
            configured_adb_path = self.settings_repository.save_adb_path(
                adb_path.text()
            )
            try:
                resolved_adb_path = ADBClient._resolve_adb(configured_adb_path)
            except Exception as error:
                QMessageBox.warning(
                    self,
                    "ADB 路径无效",
                    f"基本设置已保存，但当前无法找到 adb：\n{error}",
                )
                return
            self.adb_client.adb_path = resolved_adb_path
            self.device_page.scan_status.setText(f"ADB：{resolved_adb_path}")
            self.device_page.scan_devices()
            timezone_combo.setCurrentText(timezone_name)
            self.user_page.refresh_users()
            self._info(
                "保存成功",
                f"列表显示时区已设置为 {timezone_name}。\nADB：{resolved_adb_path}",
            )

        save.clicked.connect(save_basic_settings)
        content.addWidget(save)
        layout.addWidget(card)
        layout.addStretch()
        return page

    def _llm_settings(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(0, 16, 0, 0)
        card, card_content = card_layout()
        card_content.addWidget(label("模型服务", "SectionTitle"))
        form = QFormLayout(); form.setSpacing(13)
        provider = QComboBox(); provider.addItems(["OpenAI 兼容接口", "OpenAI", "自定义服务"])
        base_url = QLineEdit("https://api.openai.com/v1")
        key = QLineEdit(); key.setEchoMode(QLineEdit.EchoMode.Password); key.setPlaceholderText("输入 API Key")
        model = QComboBox(); model.setEditable(True); model.addItems(["gpt-5-mini", "gpt-5", "自定义模型"])
        temperature = QSpinBox(); temperature.setRange(0, 100); temperature.setValue(30); temperature.setSuffix(" %")
        timeout = QSpinBox(); timeout.setRange(5, 180); timeout.setValue(45); timeout.setSuffix(" 秒")
        form.addRow("服务提供商", provider); form.addRow("Base URL", base_url); form.addRow("API Key", key); form.addRow("模型名称", model); form.addRow("生成灵活度", temperature); form.addRow("请求超时", timeout)
        card_content.addLayout(form)
        buttons = QHBoxLayout(); test = QPushButton("测试连接"); test.clicked.connect(lambda: self._info("模型连接", "当前是 UI 原型，尚未发起网络请求。")); buttons.addWidget(test); buttons.addStretch(); save = QPushButton("保存设置"); save.setObjectName("Primary"); buttons.addWidget(save); card_content.addLayout(buttons)
        layout.addWidget(card)
        prompt, prompt_layout = card_layout(); prompt_layout.addWidget(label("回复策略", "SectionTitle")); prompt_layout.addWidget(label("系统提示词", "Muted")); editor = QPlainTextEdit("你是专业、友善的客户沟通助手。根据用户意图和对话历史生成简洁回复；不得虚构价格、库存或服务承诺。涉及投诉、法律或隐私时转交人工处理。"); editor.setMinimumHeight(120); prompt_layout.addWidget(editor); prompt_layout.addWidget(QCheckBox("发送前执行第二次安全检查")); prompt_layout.addWidget(QCheckBox("低置信度回复进入人工审核")); layout.addWidget(prompt); layout.addStretch(); return page

    def _automation_settings(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(0, 16, 0, 0)
        saved = self.settings_repository.get_setting("automation", {})
        defaults = {"daily_follow_limit": 100, "daily_message_limit": 60, "contact_cooldown_days": 30, "max_consecutive_errors": 5, "pause_on_security_check": True, "review_first_message": True}
        config = defaults | saved
        limits, limits_layout = card_layout(); limits_layout.addWidget(label("每日操作限制", "SectionTitle")); form = QFormLayout()
        fields: dict[str, QSpinBox] = {}
        definitions = [("daily_follow_limit", "单设备关注上限", 500), ("daily_message_limit", "单设备私信上限", 300), ("contact_cooldown_days", "同一用户冷却期", 180), ("max_consecutive_errors", "连续错误停止阈值", 20)]
        for key, title, maximum in definitions:
            spin = QSpinBox(); spin.setRange(1, maximum); spin.setValue(int(config[key])); spin.setSuffix(" 天" if key == "contact_cooldown_days" else "")
            fields[key] = spin; form.addRow(title, spin)
        limits_layout.addLayout(form)
        pause_check = QCheckBox("触发验证码或安全验证时立即暂停设备"); pause_check.setChecked(bool(config["pause_on_security_check"]))
        review_check = QCheckBox("所有首条私信必须人工批准"); review_check.setChecked(bool(config["review_first_message"]))
        limits_layout.addWidget(pause_check); limits_layout.addWidget(review_check)
        save = QPushButton("保存自动化规则"); save.setObjectName("Primary")
        def save_automation() -> None:
            values = {key: spin.value() for key, spin in fields.items()}
            values.update({"pause_on_security_check": pause_check.isChecked(), "review_first_message": review_check.isChecked()})
            self.settings_repository.set_setting("automation", values)
            self._info("保存成功", "自动化规则已保存到数据库。")
        save.clicked.connect(save_automation); limits_layout.addWidget(save)
        layout.addWidget(limits); layout.addStretch(); return page

    def _notification_settings(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(0, 16, 0, 0)
        saved = self.settings_repository.get_setting("notification", {})
        card, content = card_layout(); content.addWidget(label("推送渠道", "SectionTitle")); channel = QComboBox(); channel.addItems(["企业微信 Webhook", "钉钉 Webhook", "Telegram", "电子邮件"]); channel.setCurrentText(saved.get("channel", "企业微信 Webhook")); endpoint = QLineEdit(saved.get("endpoint", "")); endpoint.setPlaceholderText("Webhook URL 或接收地址"); form = QFormLayout(); form.addRow("渠道", channel); form.addRow("接收地址", endpoint); content.addLayout(form)
        high_intent = QCheckBox("高意向用户发送新消息时推送"); high_intent.setChecked(saved.get("high_intent", True))
        device_error = QCheckBox("设备离线或任务连续失败时推送"); device_error.setChecked(saved.get("device_error", True))
        sensitive = QCheckBox("敏感对话进入人工队列时推送"); sensitive.setChecked(saved.get("sensitive", True))
        content.addWidget(high_intent); content.addWidget(device_error); content.addWidget(sensitive)
        button = QPushButton("保存推送设置"); button.setObjectName("Primary")
        def save_notification() -> None:
            self.settings_repository.set_setting("notification", {"channel": channel.currentText(), "endpoint": endpoint.text().strip(), "high_intent": high_intent.isChecked(), "device_error": device_error.isChecked(), "sensitive": sensitive.isChecked()})
            self._info("保存成功", "消息推送设置已保存到数据库。")
        button.clicked.connect(save_notification); content.addWidget(button); layout.addWidget(card); layout.addStretch(); return page

    @staticmethod
    def _table(headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers)); table.setHorizontalHeaderLabels(headers); table.setAlternatingRowColors(True); table.setShowGrid(False); table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers); table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); table.verticalHeader().setVisible(False); table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch); return table

    @staticmethod
    def _fill_table(table: QTableWidget, rows: list[list[str]]) -> None:
        table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            table.setRowHeight(row_index, 48)
            for column_index, value in enumerate(row):
                item = QTableWidgetItem(value)
                if value in {"运行中", "已完成", "空闲"}: item.setForeground(QColor("#54e0ac"))
                elif value in {"离线", "已停止"}: item.setForeground(QColor("#ff8fa0"))
                table.setItem(row_index, column_index, item)

    def _load_mock_chat(self, name: str) -> None:
        while self.chat_area.count():
            child = self.chat_area.takeAt(0)
            if child.widget(): child.widget().deleteLater()
        messages = [
            (False, "请问这个帐篷在哪里买？适合两个人吗？", "10:31"),
            (True, "你好！这款是双人轻量帐篷，适合周末露营使用。你更看重重量还是内部空间呢？", "10:33 · AI 自动回复"),
            (False, "重量优先，另外想问一下价格。", "10:36"),
        ]
        for outbound, text, time in messages:
            bubble = QLabel(f"{text}\n\n{time}"); bubble.setWordWrap(True); bubble.setMaximumWidth(430); bubble.setStyleSheet(f"background:{'#1d4f91' if outbound else '#15253b'}; color:#eef5ff; padding:11px 13px; border-radius:11px;")
            holder = QWidget(); row = QHBoxLayout(holder); row.setContentsMargins(0, 2, 0, 2)
            if outbound: row.addStretch(); row.addWidget(bubble)
            else: row.addWidget(bubble); row.addStretch()
            self.chat_area.addWidget(holder)

    def _contact_changed(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        if not current: return
        name, handle, message, tag = current.data(Qt.ItemDataRole.UserRole)
        self.chat_name.setText(name); self.profile_name.setText(name)

    def _send_mock_reply(self) -> None:
        if not self.reply_box.toPlainText().strip(): return
        self._info("模拟发送成功", "回复已添加到 UI 演示流程；当前未连接真实 TikTok。")

    def _toast_refresh(self) -> None:
        self._refresh_dashboard_data()
        if hasattr(self, "device_page"):
            self.device_page.scan_devices()
        self.breadcrumb.setText("工作台  /  运营总览  ·  刚刚更新")
        QTimer.singleShot(1800, lambda: self.breadcrumb.setText("工作台  /  运营总览"))

    def _info(self, title: str, text: str) -> None:
        QMessageBox.information(self, title, text)

    def closeEvent(self, event) -> None:
        self.task_page.shutdown()
        self.automation_page.shutdown()
        for worker in list(self.task_page.workers.values()):
            worker.wait(3_000)
        super().closeEvent(event)
