"""Paginated view of collected users before intent screening."""

from __future__ import annotations

from math import ceil

import requests
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QProgressDialog,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from services.intent_classifier import SYSTEM_PROMPT, build_intent_prompt, parse_intent_result
from services.model_test_client import ModelTestClient, ModelTestError
from storage import SettingsRepository, UserRepository
from ui.intent_filter_dialog import IntentFilterDialog
from ui.widgets import SectionHeader, card_layout, label
from utils.time_utils import format_utc_timestamp


TAG_COLORS = [
    ("#163d68", "#9ac7ff"),
    ("#174b3b", "#83e6bd"),
    ("#55391a", "#ffd28a"),
    ("#4c285d", "#e6a7ff"),
    ("#532c3b", "#ffabc4"),
]


class IntentFilterWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        users: list[dict],
        rule_prompt: str,
        provider: dict,
        model: dict,
        proxy_settings: dict,
    ) -> None:
        super().__init__()
        self.users = users
        self.rule_prompt = rule_prompt
        self.provider = provider
        self.model = model
        self.proxy_settings = proxy_settings
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            prompt = build_intent_prompt(self.rule_prompt, self.users)
            response = "".join(ModelTestClient().stream_test(
                self.provider,
                self.model,
                prompt,
                SYSTEM_PROMPT,
                stream=False,
                cancelled=lambda: self._cancelled,
                proxy_settings=self.proxy_settings,
            ))
            if self._cancelled:
                self.failed.emit("意向判断已取消")
                return
            expected_ids = {int(item["id"]) for item in self.users}
            intent_ids, non_intent_ids = parse_intent_result(response, expected_ids)
            self.succeeded.emit({
                "intent_ids": intent_ids,
                "non_intent_ids": non_intent_ids,
            })
        except (ModelTestError, requests.RequestException, ValueError) as error:
            self.failed.emit(str(error))
        except Exception as error:
            self.failed.emit(f"意向判断失败：{error}")


class UserManagementWidget(QWidget):
    PAGE_SIZE = 30

    def __init__(
        self,
        repository: UserRepository,
        settings_repository: SettingsRepository,
    ) -> None:
        super().__init__()
        self.repository = repository
        self.settings_repository = settings_repository
        self.page = 1
        self.total = 0
        self.intent_worker: IntentFilterWorker | None = None
        self.intent_progress: QProgressDialog | None = None
        self._build_ui()
        self.refresh_users()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 28)
        layout.setSpacing(18)

        header = QHBoxLayout()
        header.addWidget(SectionHeader(
            "用户管理",
            "查看自动化任务采集的未筛选用户；标记显示用户来源",
        ))
        header.addStretch()
        self.total_label = label("共 0 位用户", "Muted")
        header.addWidget(self.total_label)
        self.intent_button = QPushButton("筛选意向用户")
        self.intent_button.setObjectName("Primary")
        self.intent_button.clicked.connect(self.start_intent_filter)
        header.addWidget(self.intent_button)
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh_users)
        header.addWidget(refresh)
        layout.addLayout(header)

        filters, filters_layout = card_layout(12)
        filter_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索用户名、@名字或用户标签")
        self.search_edit.returnPressed.connect(self.apply_filters)
        self.mark_combo = QComboBox()
        self.mark_combo.addItem("全部标记", "")
        self.mark_combo.addItem("视频", "视频")
        self.mark_combo.addItem("直播", "直播")
        self.mark_combo.addItem("意向", "意向")
        self.mark_combo.currentIndexChanged.connect(self.apply_filters)
        search_button = QPushButton("查询")
        search_button.setObjectName("Primary")
        search_button.clicked.connect(self.apply_filters)
        reset_button = QPushButton("重置")
        reset_button.clicked.connect(self.reset_filters)
        filter_row.addWidget(self.search_edit, 1)
        filter_row.addWidget(self.mark_combo)
        filter_row.addWidget(search_button)
        filter_row.addWidget(reset_button)
        filters_layout.addLayout(filter_row)
        layout.addWidget(filters)

        table_card, table_layout = card_layout()
        self.table = QTableWidget(0, 11)
        self.table.setObjectName("UserManagementTable")
        self.table.setHorizontalHeaderLabels([
            "用户名", "@名字", "标记", "关注", "粉丝", "赞", "留言数",
            "首次消息", "用户标签", "最近采集", "操作",
        ])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(8, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(9, QHeaderView.ResizeMode.Fixed)
        header_view.resizeSection(9, 180)
        header_view.setSectionResizeMode(10, QHeaderView.ResizeMode.Fixed)
        header_view.resizeSection(10, 128)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.setMinimumHeight(360)
        table_layout.addWidget(self.table)

        pagination = QHBoxLayout()
        select_all_button = QPushButton("全选当前页")
        select_all_button.clicked.connect(self.table.selectAll)
        self.batch_intent_button = QPushButton("批量标记意向")
        self.batch_intent_button.clicked.connect(self.mark_selected_intent)
        self.batch_delete_button = QPushButton("批量删除")
        self.batch_delete_button.setObjectName("DangerButton")
        self.batch_delete_button.clicked.connect(self.delete_selected_users)
        pagination.addWidget(select_all_button)
        pagination.addWidget(self.batch_intent_button)
        pagination.addWidget(self.batch_delete_button)
        self.prev_button = QPushButton("上一页")
        self.prev_button.clicked.connect(self.previous_page)
        self.page_label = label("第 1 / 1 页", "Muted")
        self.next_button = QPushButton("下一页")
        self.next_button.clicked.connect(self.next_page)
        pagination.addStretch()
        pagination.addWidget(self.prev_button)
        pagination.addWidget(self.page_label)
        pagination.addWidget(self.next_button)
        table_layout.addLayout(pagination)
        layout.addWidget(table_card)

        detail_card, detail_layout = card_layout()
        detail_layout.addWidget(label("用户采集留言", "SectionTitle"))
        self.comments_view = QPlainTextEdit()
        self.comments_view.setReadOnly(True)
        self.comments_view.setPlaceholderText("选择用户后显示其采集留言")
        self.comments_view.setMinimumHeight(140)
        detail_layout.addWidget(self.comments_view)
        layout.addWidget(detail_card)
        layout.addStretch()

    def apply_filters(self) -> None:
        self.page = 1
        self.refresh_users()

    def reset_filters(self) -> None:
        self.search_edit.clear()
        self.mark_combo.setCurrentIndex(0)
        self.page = 1
        self.refresh_users()

    def refresh_users(self) -> None:
        users, self.total = self.repository.list_users(
            page=self.page,
            page_size=self.PAGE_SIZE,
            search=self.search_edit.text(),
            mark=str(self.mark_combo.currentData() or ""),
        )
        page_count = max(1, ceil(self.total / self.PAGE_SIZE))
        if self.page > page_count:
            self.page = page_count
            users, self.total = self.repository.list_users(
                page=self.page,
                page_size=self.PAGE_SIZE,
                search=self.search_edit.text(),
                mark=str(self.mark_combo.currentData() or ""),
            )
        self.table.setRowCount(len(users))
        timezone_name = self.settings_repository.get_timezone()
        header_metrics = self.table.horizontalHeader().fontMetrics()
        cell_metrics = self.table.fontMetrics()
        recent_column_width = header_metrics.horizontalAdvance("最近采集") + 24
        for row, user in enumerate(users):
            recent_text = format_utc_timestamp(user["last_seen_at"], timezone_name)
            values = [
                user["username"], user["handle"], user["mark"],
                user["following"], user["followers"], user["likes"],
                str(user["comment_count"]),
                "是" if user["first_message_sent"] else "否", "",
                recent_text,
                "",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, int(user["id"]))
                if column == 2:
                    colors = {
                        "意向": Qt.GlobalColor.green,
                        "直播": Qt.GlobalColor.magenta,
                        "视频": Qt.GlobalColor.cyan,
                    }
                    item.setForeground(colors.get(str(value), Qt.GlobalColor.white))
                self.table.setItem(row, column, item)
            self.table.setCellWidget(row, 8, self._tag_widget(user["tags"]))
            self.table.setCellWidget(
                row, 10, self._operation_widget(int(user["id"]), user["mark"])
            )
            self.table.setRowHeight(row, 40)
            recent_column_width = max(
                recent_column_width,
                cell_metrics.horizontalAdvance(recent_text) + 18,
            )
        self.table.horizontalHeader().resizeSection(9, recent_column_width)
        self.total_label.setText(f"共 {self.total} 位用户")
        self.page_label.setText(f"第 {self.page} / {page_count} 页 · 每页 {self.PAGE_SIZE} 条")
        self.prev_button.setEnabled(self.page > 1)
        self.next_button.setEnabled(self.page < page_count)
        if users:
            self.table.selectRow(0)
        else:
            self.comments_view.clear()

    @staticmethod
    def _tag_widget(tags: list[str]) -> QWidget:
        container = QWidget()
        # QTableWidget otherwise shrinks cell widgets to their size hint
        # (about 19 px under the app stylesheet), clipping pills vertically.
        container.setMinimumHeight(28)
        estimated_width = (
            12 + sum(max(38, len(tag) * 13 + 18) for tag in tags)
            + max(0, len(tags) - 1) * 3
        )
        container.setMinimumWidth(max(180, estimated_width))
        row = QHBoxLayout(container)
        row.setContentsMargins(2, 0, 2, 0)
        row.setSpacing(3)
        row.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        for tag in tags:
            background, foreground = TAG_COLORS[sum(ord(char) for char in tag) % len(TAG_COLORS)]
            item = QLabel(tag)
            item.setFixedHeight(20)
            item.setAlignment(Qt.AlignmentFlag.AlignCenter)
            item.setStyleSheet(
                f"background:{background}; color:{foreground}; border-radius:7px; "
                "padding:0px 6px; font-size:11px;"
            )
            row.addWidget(item)
        row.addStretch()
        return container

    def _operation_widget(self, user_id: int, mark: str) -> QWidget:
        container = QWidget()
        container.setMinimumHeight(28)
        row = QHBoxLayout(container)
        row.setContentsMargins(2, 0, 2, 0)
        row.setSpacing(4)
        row.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        intent = QPushButton("意向")
        intent.setObjectName("CompactActionButton")
        intent.setFixedSize(54, 24)
        intent.setEnabled(mark != "意向")
        intent.clicked.connect(lambda: self.mark_user_intent(user_id))
        delete = QPushButton("删除")
        delete.setObjectName("CompactDangerButton")
        delete.setFixedSize(48, 24)
        delete.clicked.connect(lambda: self.delete_users([user_id]))
        row.addWidget(intent)
        row.addWidget(delete)
        return container

    def selected_user_id(self) -> int | None:
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        value = item.data(Qt.ItemDataRole.UserRole) if item else None
        return int(value) if value is not None else None

    def selected_user_ids(self) -> list[int]:
        result: list[int] = []
        for index in self.table.selectionModel().selectedRows(0):
            item = self.table.item(index.row(), 0)
            value = item.data(Qt.ItemDataRole.UserRole) if item else None
            if value is not None:
                result.append(int(value))
        return sorted(set(result))

    def mark_user_intent(self, user_id: int) -> None:
        self.repository.update_mark(user_id, "意向")
        self.refresh_users()

    def mark_selected_intent(self) -> None:
        user_ids = self.selected_user_ids()
        if not user_ids:
            QMessageBox.information(self, "批量标记", "请先选择至少一位用户。")
            return
        count = self.repository.update_users_mark(user_ids, "意向")
        self.refresh_users()
        QMessageBox.information(self, "批量标记完成", f"已将 {count} 位用户标记为意向。")

    def delete_users(self, user_ids: list[int]) -> None:
        if not user_ids:
            return
        answer = QMessageBox.question(
            self,
            "删除用户",
            f"确定删除选中的 {len(user_ids)} 位用户及其全部采集留言吗？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        count = self.repository.delete_users(user_ids)
        self.refresh_users()
        QMessageBox.information(self, "删除完成", f"已删除 {count} 位用户。")

    def delete_selected_users(self) -> None:
        user_ids = self.selected_user_ids()
        if not user_ids:
            QMessageBox.information(self, "批量删除", "请先选择至少一位用户。")
            return
        self.delete_users(user_ids)

    def _selection_changed(self) -> None:
        user_id = self.selected_user_id()
        if user_id is None:
            self.comments_view.clear()
            return
        comments = self.repository.list_comments(user_id)
        timezone_name = self.settings_repository.get_timezone()
        self.comments_view.setPlainText("\n".join(
            f'[{format_utc_timestamp(item["collected_at"], timezone_name)}] '
            f'标签：{item["keyword"]}\n{item["comment"]}'
            for item in comments
        ))

    def start_intent_filter(self) -> None:
        if self.intent_worker is not None:
            return
        users = self.repository.list_collected_users_for_intent()
        if not users:
            QMessageBox.information(self, "筛选意向用户", "当前没有来源为“视频”或“直播”的用户。")
            return
        dialog = IntentFilterDialog(self.settings_repository, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        model_id, rule_prompt = dialog.values()
        model = self.settings_repository.get_model(model_id)
        if not model or model["model_type"] != "llm" or not model["enabled"]:
            QMessageBox.warning(self, "无法开始判断", "所选大语言模型不可用。")
            return
        provider = self.settings_repository.get_provider(model["provider_id"], reveal_key=True)
        if not provider:
            QMessageBox.warning(self, "无法开始判断", "模型所属的 API 厂家不存在。")
            return
        self.intent_progress = QProgressDialog(
            f"正在将 {len(users)} 位视频/直播用户的信息与留言提交给模型判断……",
            "取消",
            0,
            0,
            self,
        )
        self.intent_progress.setWindowTitle("意向判断")
        self.intent_progress.setWindowModality(Qt.WindowModality.WindowModal)
        self.intent_progress.setMinimumDuration(0)
        self.intent_button.setEnabled(False)
        self.intent_worker = IntentFilterWorker(
            users,
            rule_prompt,
            provider,
            model,
            self.settings_repository.get_proxy_settings(reveal_password=True),
        )
        self.intent_progress.canceled.connect(self.intent_worker.cancel)
        self.intent_worker.succeeded.connect(self._intent_filter_succeeded)
        self.intent_worker.failed.connect(self._intent_filter_failed)
        self.intent_worker.finished.connect(self._intent_worker_finished)
        self.intent_worker.start()

    def _intent_filter_succeeded(self, result: dict) -> None:
        if self.intent_progress:
            self.intent_progress.close()
        intent_ids = [int(item) for item in result["intent_ids"]]
        non_intent_ids = [int(item) for item in result["non_intent_ids"]]
        answer = QMessageBox.question(
            self,
            "意向判断完成",
            f"模型判断完成：\n\n"
            f"意向用户：{len(intent_ids)} 位，将保留原来源标记\n"
            f"非意向用户：{len(non_intent_ids)} 位，将从临时用户库删除\n\n"
            "是否执行以上操作？选择“否”不会修改任何数据。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            QMessageBox.information(self, "未执行", "判断结果已放弃，用户数据没有发生变化。")
            return
        kept, deleted = self.repository.apply_intent_results(intent_ids, non_intent_ids)
        self.refresh_users()
        QMessageBox.information(
            self,
            "操作完成",
            f"已保留 {kept} 位意向用户（来源标记未改变），删除 {deleted} 位非意向用户。",
        )

    def _intent_filter_failed(self, message: str) -> None:
        if self.intent_progress:
            self.intent_progress.close()
        QMessageBox.warning(self, "意向判断失败", message + "\n\n用户数据没有发生变化。")

    def _intent_worker_finished(self) -> None:
        worker = self.intent_worker
        self.intent_worker = None
        self.intent_button.setEnabled(True)
        if self.intent_progress:
            self.intent_progress.deleteLater()
            self.intent_progress = None
        if worker:
            worker.deleteLater()

    def shutdown(self) -> None:
        if self.intent_worker and self.intent_worker.isRunning():
            self.intent_worker.cancel()

    def previous_page(self) -> None:
        if self.page > 1:
            self.page -= 1
            self.refresh_users()

    def next_page(self) -> None:
        if self.page * self.PAGE_SIZE < self.total:
            self.page += 1
            self.refresh_users()
