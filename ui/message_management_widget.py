"""Database-backed contacts and chat-style direct-message history page."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from storage import ConversationRepository, SettingsRepository
from ui.widgets import SectionHeader, card_layout, label
from utils.time_utils import format_utc_timestamp


MESSAGE_KIND_LABELS = {
    "opening": "启动消息",
    "model_reply": "自动回复",
    "received": "文本",
    "received_text": "文本",
    "received_emoji": "表情",
    "received_sticker": "贴纸",
    "received_gif": "GIF",
    "received_image": "图片",
    "received_voice": "语音",
    "received_shared_card": "分享卡片",
    "received_unknown_media": "媒体消息",
}


class ChatBubble(QFrame):
    """One message bubble aligned by its direction in the parent row."""

    def __init__(self, message: dict, timezone: str) -> None:
        super().__init__()
        outbound = message["direction"] == "outbound"
        self.setObjectName("OutgoingBubble" if outbound else "IncomingBubble")
        self.setMaximumWidth(560)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)

        body = QVBoxLayout(self)
        body.setContentsMargins(13, 9, 13, 8)
        body.setSpacing(5)

        content = QLabel(str(message["content"]))
        content.setObjectName("ChatMessageText")
        content.setWordWrap(True)
        content.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.addWidget(content)

        timestamp = format_utc_timestamp(message["created_at"], timezone)
        kind = MESSAGE_KIND_LABELS.get(
            str(message.get("message_kind", "message")),
            str(message.get("message_kind", "message")),
        )
        job = message.get("job_id") or "-"
        meta = QLabel(f"{timestamp}  ·  {kind}  ·  任务 #{job}")
        meta.setObjectName("ChatMessageMeta")
        meta.setAlignment(
            Qt.AlignmentFlag.AlignRight if outbound else Qt.AlignmentFlag.AlignLeft
        )
        body.addWidget(meta)


class MessageManagementWidget(QWidget):
    def __init__(
        self, repository: ConversationRepository, settings: SettingsRepository
    ) -> None:
        super().__init__()
        self.repository = repository
        self.settings = settings

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 28)
        layout.setSpacing(18)

        head = QHBoxLayout()
        head.addWidget(SectionHeader("消息管理", "查看自动化任务真实发送和接收的私信记录"))
        head.addStretch()
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索联系人、@用户名或消息内容")
        self.search.returnPressed.connect(self.refresh)
        head.addWidget(self.search)
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh)
        head.addWidget(refresh)
        layout.addLayout(head)

        splitter = QSplitter()
        splitter.setChildrenCollapsible(False)

        left, left_body = card_layout()
        self.count = label("联系人 0", "SectionTitle")
        left_body.addWidget(self.count)
        self.contacts = QListWidget()
        self.contacts.setObjectName("MessageContactList")
        self.contacts.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.contacts.currentItemChanged.connect(self._selected)
        left_body.addWidget(self.contacts)
        splitter.addWidget(left)

        right, right_body = card_layout(0)
        chat_header = QWidget()
        chat_header.setObjectName("ChatHeader")
        chat_header_layout = QVBoxLayout(chat_header)
        chat_header_layout.setContentsMargins(18, 14, 18, 13)
        chat_header_layout.setSpacing(2)
        self.title = label("请选择联系人", "SectionTitle")
        self.subtitle = label("选择左侧联系人查看聊天记录", "Small")
        chat_header_layout.addWidget(self.title)
        chat_header_layout.addWidget(self.subtitle)
        right_body.addWidget(chat_header)

        self.history = QScrollArea()
        self.history.setObjectName("ChatHistory")
        self.history.setWidgetResizable(True)
        self.history.setFrameShape(QFrame.Shape.NoFrame)
        self.chat_canvas = QWidget()
        self.chat_canvas.setObjectName("ChatCanvas")
        self.chat_layout = QVBoxLayout(self.chat_canvas)
        self.chat_layout.setContentsMargins(18, 18, 18, 18)
        self.chat_layout.setSpacing(10)
        self.history.setWidget(self.chat_canvas)
        right_body.addWidget(self.history, 1)
        splitter.addWidget(right)

        splitter.setSizes([330, 860])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)
        self.refresh()

    def refresh(self) -> None:
        selected = self.selected_id()
        rows = self.repository.list_contacts(self.search.text())
        self.contacts.clear()
        self.count.setText(f"联系人 {len(rows)}")
        for row in rows:
            name = row["display_name"] or row["handle"] or "未知用户"
            handle = row["handle"]
            preview = str(row["latest_message"] or "").replace("\n", " ")
            if len(preview) > 30:
                preview = preview[:30] + "…"
            item = QListWidgetItem(
                f'{name}\n{handle} · {row["message_count"]} 条消息\n{preview}'
            )
            item.setData(Qt.ItemDataRole.UserRole, row)
            self.contacts.addItem(item)
            if row["id"] == selected:
                self.contacts.setCurrentItem(item)
        if self.contacts.count() and self.contacts.currentRow() < 0:
            self.contacts.setCurrentRow(0)
        if not rows:
            self.title.setText("请选择联系人")
            self.subtitle.setText("选择左侧联系人查看聊天记录")
            self._show_empty("暂无真实私信记录")

    def selected_id(self) -> int | None:
        item = self.contacts.currentItem()
        data = item.data(Qt.ItemDataRole.UserRole) if item else None
        return int(data["id"]) if data else None

    def _selected(self, current: QListWidgetItem | None, previous) -> None:
        if not current:
            return
        contact = current.data(Qt.ItemDataRole.UserRole)
        name = contact["display_name"] or contact["handle"] or "未知用户"
        handle = contact["handle"] or "暂无 @用户名"
        self.title.setText(name)
        self.subtitle.setText(f'{handle}  ·  {contact["message_count"]} 条消息')

        self._clear_chat()
        timezone = self.settings.get_timezone()
        messages = self.repository.list_messages(contact["id"])
        if not messages:
            self._show_empty("暂无真实私信记录")
            return
        self.chat_layout.addStretch(1)
        for message in messages:
            row = QWidget()
            row.setObjectName("ChatMessageRow")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            if message["direction"] == "outbound":
                row_layout.addStretch(1)
                row_layout.addWidget(ChatBubble(message, timezone), 0)
            else:
                row_layout.addWidget(ChatBubble(message, timezone), 0)
                row_layout.addStretch(1)
            self.chat_layout.addWidget(row)
        QTimer.singleShot(0, self._scroll_to_bottom)

    def _clear_chat(self) -> None:
        while self.chat_layout.count():
            item = self.chat_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _show_empty(self, text: str) -> None:
        self._clear_chat()
        empty = QLabel(text)
        empty.setObjectName("ChatEmpty")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.chat_layout.addStretch(1)
        self.chat_layout.addWidget(empty)
        self.chat_layout.addStretch(1)

    def _scroll_to_bottom(self) -> None:
        bar = self.history.verticalScrollBar()
        bar.setValue(bar.maximum())
