"""Main desktop window and prototype pages."""

from __future__ import annotations

from functools import partial

from PySide6.QtCore import QTime, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
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
    QProgressBar,
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
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from storage.settings_repository import SettingsRepository
from ui.ai_settings_widget import AISettingsWidget
from ui.proxy_settings_widget import ProxySettingsWidget
from ui.widgets import DeviceStatusCard, MetricCard, MiniChart, SectionHeader, card_layout, label


NAV_ITEMS = [
    ("OV", "运营总览"),
    ("DV", "设备管理"),
    ("TK", "任务中心"),
    ("CT", "联系人"),
    ("RV", "审核中心"),
    ("ST", "系统设置"),
]

NOTIFICATION_SETTINGS_ENABLED = False


class MainWindow(QMainWindow):
    def __init__(self, settings_repository: SettingsRepository | None = None) -> None:
        super().__init__()
        self.settings_repository = settings_repository or SettingsRepository()
        self.setWindowTitle("AutoAgent · Android 智能运营中心")
        self.resize(1480, 900)
        self.setMinimumSize(1180, 720)
        self.nav_buttons: list[QPushButton] = []
        self._build_ui()
        self._switch_page(0)

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
        self.pages.addWidget(self._scroll_page(self._devices_page()))
        self.pages.addWidget(self._scroll_page(self._tasks_page()))
        self.pages.addWidget(self._contacts_page())
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

        for index, (icon, title) in enumerate(NAV_ITEMS):
            button = QPushButton(f"{icon}    {title}")
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
        status_layout.addWidget(label("3 台设备在线 · 模拟数据", "Small"))
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
        search.setPlaceholderText("搜索联系人、任务或设备...")
        search.setFixedWidth(270)
        layout.addWidget(search)
        notice = QPushButton("●  3 条待处理")
        notice.clicked.connect(lambda: self._switch_page(4))
        layout.addWidget(notice)
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
        self.breadcrumb.setText(f"工作台  /  {NAV_ITEMS[index][1]}")

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
        header_row.addWidget(SectionHeader("运营总览", "查看今日设备运行、用户触达与会话进展"))
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
            ("在线设备", "3 / 4", "比昨日增加 1 台", "blue"),
            ("今日发现用户", "186", "↑ 12.4% 较昨日", "green"),
            ("今日新增关注", "42", "任务额度剩余 58", "orange"),
            ("待处理会话", "8", "3 条需要人工审核", "pink"),
        ]
        for index, values in enumerate(metric_data):
            metrics.addWidget(MetricCard(*values), 0, index)
        layout.addLayout(metrics)

        middle = QHBoxLayout()
        trend, trend_layout = card_layout()
        trend_header = QHBoxLayout()
        trend_header.addWidget(label("近 7 日用户触达", "SectionTitle"))
        trend_header.addStretch()
        trend_header.addWidget(label("● 发现用户", "PillBlue"))
        trend_layout.addLayout(trend_header)
        trend_layout.addWidget(MiniChart([74, 108, 96, 147, 132, 169, 186]))
        days = QHBoxLayout()
        for day in ["周一", "周二", "周三", "周四", "周五", "周六", "今天"]:
            item = label(day, "Small")
            item.setAlignment(Qt.AlignmentFlag.AlignCenter)
            days.addWidget(item)
        trend_layout.addLayout(days)
        middle.addWidget(trend, 3)

        activity, activity_layout = card_layout()
        activity_layout.addWidget(label("实时动态", "SectionTitle"))
        events = [
            ("●", "Pixel 7", "发现高意向用户 @maya_design", "刚刚", "#34d399"),
            ("●", "Galaxy S22", "发送首条消息成功", "2 分钟前", "#3b82f6"),
            ("●", "Xiaomi 13", "收到一条新私信", "6 分钟前", "#f59e0b"),
            ("●", "AI 分析", "1 条会话转入人工审核", "11 分钟前", "#f472b6"),
        ]
        for icon, source, text, when, color in events:
            row = QHBoxLayout()
            dot = QLabel(icon)
            dot.setStyleSheet(f"color:{color};")
            row.addWidget(dot)
            info = QVBoxLayout()
            info.addWidget(label(f"{source} · {text}"))
            info.addWidget(label(when, "Small"))
            row.addLayout(info)
            row.addStretch()
            activity_layout.addLayout(row)
        activity_layout.addStretch()
        middle.addWidget(activity, 2)
        layout.addLayout(middle)

        layout.addWidget(label("设备运行状态", "SectionTitle"))
        device_grid = QGridLayout()
        device_grid.setHorizontalSpacing(14)
        device_grid.addWidget(DeviceStatusCard("Pixel 7", "Android 15 · A01", "运行中", "TikTok · 搜索：露营装备", 68), 0, 0)
        device_grid.addWidget(DeviceStatusCard("Galaxy S22", "Android 14 · A02", "运行中", "TikTok · 处理未读私信", 41), 0, 1)
        device_grid.addWidget(DeviceStatusCard("Xiaomi 13", "Android 15 · A03", "空闲", "等待下一个计划任务", 0), 0, 2)
        layout.addLayout(device_grid)
        layout.addStretch()
        return page

    def _devices_page(self) -> QWidget:
        page, layout = self._page()
        row = QHBoxLayout()
        row.addWidget(SectionHeader("设备管理", "连接、监控并分配 Android 设备"))
        row.addStretch()
        scan = QPushButton("扫描 USB 设备")
        scan.clicked.connect(lambda: self._info("设备扫描", "UI 原型暂未连接 ADB，已保留扫描入口。"))
        scan.setObjectName("Primary")
        row.addWidget(scan)
        layout.addLayout(row)

        summary, summary_layout = card_layout()
        sr = QHBoxLayout()
        for value, text, color in [("4", "设备总数", "#7db4ff"), ("3", "在线", "#54e0ac"), ("2", "运行中", "#f5c46f"), ("1", "离线", "#ff8fa0")]:
            block = QVBoxLayout()
            value_label = label(value, "Metric")
            value_label.setStyleSheet(f"color:{color}; font-size:25px; font-weight:700;")
            block.addWidget(value_label)
            block.addWidget(label(text, "Muted"))
            sr.addLayout(block)
            sr.addStretch()
        summary_layout.addLayout(sr)
        layout.addWidget(summary)

        table = self._table(["设备", "ADB 序列号", "系统", "绑定账号", "当前任务", "状态", "操作"])
        rows = [
            ["Pixel 7", "32051FDH2009B", "Android 15", "@north_lab", "搜索：露营装备", "运行中", "查看"],
            ["Galaxy S22", "R5CT31A8", "Android 14", "@daily_home", "处理未读私信", "运行中", "查看"],
            ["Xiaomi 13", "84e3c901", "Android 15", "@light_studio", "—", "空闲", "查看"],
            ["OnePlus 10", "8A15F0C2", "Android 13", "未绑定", "—", "离线", "诊断"],
        ]
        self._fill_table(table, rows)
        table.setMinimumHeight(300)
        layout.addWidget(table)

        detail, detail_layout = card_layout()
        detail_layout.addWidget(label("设备预览 · Pixel 7", "SectionTitle"))
        details = QHBoxLayout()
        mock = QLabel("TikTok\n\n设备画面预览\n\n1080 × 2400")
        mock.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mock.setFixedSize(180, 330)
        mock.setStyleSheet("background:#050a12; border:5px solid #24334a; border-radius:24px; color:#50627a;")
        details.addWidget(mock)
        info = QVBoxLayout()
        for title, value in [("连接状态", "在线 · USB"), ("电量", "82%"), ("前台应用", "com.zhiliaoapp.musically"), ("分辨率", "1080 × 2400"), ("最近心跳", "刚刚")]:
            r = QHBoxLayout()
            r.addWidget(label(title, "Muted"))
            r.addStretch()
            r.addWidget(label(value))
            info.addLayout(r)
        info.addStretch()
        actions = QHBoxLayout()
        actions.addWidget(QPushButton("暂停任务"))
        actions.addWidget(QPushButton("人工接管"))
        actions.addWidget(QPushButton("重启应用"))
        actions.addStretch()
        info.addLayout(actions)
        details.addLayout(info, 1)
        detail_layout.addLayout(details)
        layout.addWidget(detail)
        return page

    def _tasks_page(self) -> QWidget:
        page, layout = self._page()
        row = QHBoxLayout()
        row.addWidget(SectionHeader("任务中心", "创建 TikTok 采集任务并安排执行时间"))
        row.addStretch()
        new_button = QPushButton("+  新建任务")
        new_button.setObjectName("Primary")
        new_button.clicked.connect(lambda: self._info("新建任务", "右侧任务配置区域已准备好，可在后续版本接入持久化。"))
        row.addWidget(new_button)
        layout.addLayout(row)

        splitter = QSplitter()
        active, active_layout = card_layout()
        active_layout.addWidget(label("正在执行", "SectionTitle"))
        for name, device, keyword, progress in [
            ("户外兴趣用户发现", "Pixel 7", "露营装备", 68),
            ("私信自动回复", "Galaxy S22", "收件箱轮询", 41),
        ]:
            box, box_layout = card_layout(12)
            top = QHBoxLayout()
            top.addWidget(label(name, "SectionTitle"))
            top.addStretch()
            top.addWidget(label("运行中", "PillGreen"))
            box_layout.addLayout(top)
            box_layout.addWidget(label(f"{device}  ·  {keyword}", "Muted"))
            bar = QProgressBar()
            bar.setValue(progress)
            box_layout.addWidget(bar)
            controls = QHBoxLayout()
            controls.addWidget(QPushButton("暂停"))
            controls.addWidget(QPushButton("查看日志"))
            controls.addStretch()
            box_layout.addLayout(controls)
            active_layout.addWidget(box)
        active_layout.addStretch()
        splitter.addWidget(active)

        config, config_layout = card_layout()
        config_layout.addWidget(label("TikTok 任务配置", "SectionTitle"))
        form = QFormLayout()
        form.setSpacing(12)
        task_name = QLineEdit("户外兴趣用户发现")
        app = QComboBox(); app.addItems(["TikTok", "其他应用（待扩展）"])
        devices = QComboBox(); devices.addItems(["Pixel 7", "Galaxy S22", "所有空闲设备"])
        keywords = QPlainTextEdit("露营装备\n户外旅行\n轻量帐篷")
        keywords.setMaximumHeight(86)
        start_time = QTimeEdit(QTime(9, 0)); start_time.setDisplayFormat("HH:mm")
        end_time = QTimeEdit(QTime(20, 0)); end_time.setDisplayFormat("HH:mm")
        time_row = QWidget(); time_layout = QHBoxLayout(time_row); time_layout.setContentsMargins(0, 0, 0, 0)
        time_layout.addWidget(start_time); time_layout.addWidget(label("至", "Muted")); time_layout.addWidget(end_time)
        max_comments = QSpinBox(); max_comments.setRange(10, 1000); max_comments.setValue(100)
        form.addRow("任务名称", task_name)
        form.addRow("目标应用", app)
        form.addRow("执行设备", devices)
        form.addRow("搜索关键词", keywords)
        form.addRow("执行时段", time_row)
        form.addRow("单词最大评论", max_comments)
        config_layout.addLayout(form)
        config_layout.addWidget(QCheckBox("允许关注符合条件的用户"))
        config_layout.addWidget(QCheckBox("首条私信发送前需要人工审核"))
        save = QPushButton("保存并创建任务")
        save.setObjectName("Primary")
        save.clicked.connect(lambda: self._info("任务已保存", "UI 原型已模拟保存任务，后续将接入数据库和调度器。"))
        config_layout.addWidget(save)
        config_layout.addStretch()
        splitter.addWidget(config)
        splitter.setSizes([560, 460])
        layout.addWidget(splitter)

        layout.addWidget(label("最近任务", "SectionTitle"))
        table = self._table(["任务名称", "应用", "设备", "关键词", "发现用户", "开始时间", "状态"])
        self._fill_table(table, [
            ["美妆意向用户", "TikTok", "2 台", "summer makeup", "214", "昨天 14:20", "已完成"],
            ["家居评论采集", "TikTok", "1 台", "small apartment", "87", "昨天 09:00", "已完成"],
            ["周末补充任务", "TikTok", "1 台", "camping setup", "36", "周六 18:10", "已停止"],
        ])
        table.setMinimumHeight(220)
        layout.addWidget(table)
        return page

    def _contacts_page(self) -> QWidget:
        page, layout = self._page()
        header = QHBoxLayout()
        header.addWidget(SectionHeader("联系人与私信", "查看已发现用户、关注状态和完整会话记录"))
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
        tabs.addTab(AISettingsWidget(self.settings_repository), "AI 模型服务")
        tabs.addTab(ProxySettingsWidget(self.settings_repository), "网络代理")
        tabs.addTab(self._automation_settings(), "自动化规则")
        if NOTIFICATION_SETTINGS_ENABLED:
            tabs.addTab(self._notification_settings(), "消息推送")
        layout.addWidget(tabs)
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
        self.breadcrumb.setText("工作台  /  运营总览  ·  刚刚更新")
        QTimer.singleShot(1800, lambda: self.breadcrumb.setText("工作台  /  运营总览"))

    def _info(self, title: str, text: str) -> None:
        QMessageBox.information(self, title, text)
