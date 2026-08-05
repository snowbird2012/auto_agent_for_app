"""Database-backed automation task configuration and single-runner controls."""

from __future__ import annotations

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from automation import (
    TikTokInboxListener,
    TikTokMessageWorkflow,
    WorkflowCancelled,
)
from devices import ADBClient, AndroidDevice
from services.message_strategy_service import MessageStrategyService
from storage import (
    AutomationJobRepository,
    ConversationRepository,
    MessageStrategyRepository,
    SettingsRepository,
    UserRepository,
)
from ui.widgets import SectionHeader, card_layout, label
from utils.time_utils import format_utc_timestamp


STATUS_LABELS = {
    "created": "待执行",
    "running": "执行中",
    "stopped": "已停止",
    "completed": "已完成",
    "failed": "失败",
}


def _record_messages(record: dict | None) -> list[dict[str, str]]:
    if not record:
        return []
    messages = record.get("messages")
    if isinstance(messages, list):
        return [item for item in messages if isinstance(item, dict) and item.get("content")]
    content = str(record.get("message", "")).strip()
    return [{"type": "text", "content": content}] if content else []


def _strategy_message(messages: list[dict[str, str]]) -> str:
    labels = {
        "text": "文本", "emoji": "表情", "sticker": "贴纸", "gif": "GIF",
        "image": "图片", "voice": "语音", "shared_card": "分享卡片",
        "unknown_media": "媒体消息",
    }
    return "\n".join(
        f"[{labels.get(item.get('type', 'text'), item.get('type', 'text'))}] {item['content']}"
        for item in messages
    )


class MessageDebugWorker(QThread):
    progress_changed = Signal(str, str, int)
    succeeded = Signal(str, str)
    failed = Signal(str)
    cancelled = Signal(str)
    message_recorded = Signal(object)
    messages_updated = Signal()

    def __init__(self, adb: ADBClient, serial: str, handle: str, message: str,
                 runtime: dict, conversation_repository: ConversationRepository | None = None,
                 job_id: int | None = None) -> None:
        super().__init__()
        self.adb = adb
        self.serial = serial
        self.handle = handle
        self.message = message
        self.runtime = runtime
        self.conversation_repository = conversation_repository
        self.job_id = job_id
        self.contact_handle = ""
        self.workflow: TikTokMessageWorkflow | None = None

    def stop(self) -> None:
        if self.workflow:
            self.workflow.cancel()

    def run(self) -> None:
        try:
            self.workflow = TikTokMessageWorkflow(self.adb, self.serial)
            normalized = self.workflow.run_message(
                self.handle,
                self.message,
                lambda step, text, percent: self.progress_changed.emit(
                    step, text, percent
                ),
            )
            self.contact_handle = normalized
            self.message_recorded.emit({"handle":normalized,"display_name":normalized,"direction":"outbound","message_kind":"opening","content":self.message})
            self.workflow = TikTokInboxListener(self.adb, self.serial)
            self._listen_loop()
        except WorkflowCancelled as error:
            self.cancelled.emit(str(error))
        except Exception as error:
            self.failed.emit(str(error))

    def _listen_loop(self) -> None:
        while True:
            record = self.workflow.listen_once(self._emit_progress, timeout=None)
            self._evaluate_and_reply(record)

    def _emit_progress(self, step: str, text: str, percent: int = 100) -> None:
        self.progress_changed.emit(step, text, percent)

    def _evaluate_and_reply(self, record: dict | None) -> None:
        if not record: return
        messages = _record_messages(record)
        if self.conversation_repository is not None:
            messages = self.conversation_repository.record_incoming_batch(
                handle=self.contact_handle,
                display_name=record["sender"],
                messages=messages,
                job_id=self.job_id,
            )
            if messages:
                self.messages_updated.emit()
        else:
            for item in messages:
                self.message_recorded.emit({"handle":self.contact_handle,"display_name":record["sender"],"direction":"inbound","message_kind":"received_"+item.get("type","text"),"content":item["content"]})
        if not messages:
            self._emit_progress("MESSAGE_DUPLICATE", "本轮消息均已记录，返回首页继续监听")
            self.workflow._return_to_tiktok_home()
            return
        user_message = _strategy_message(messages)
        self._emit_progress("MODEL_ANALYZE", f"正在使用策略“{self.runtime['strategy']['name']}”分析 {len(messages)} 条新消息：{user_message}")
        result = MessageStrategyService().evaluate(
            self.runtime["strategy"], self.runtime["model"], self.runtime["provider"],
            user_message, self.runtime["proxy"],
            cancelled=lambda: self.workflow.cancel_event.is_set(),
        )
        self._emit_progress("MODEL_DECISION", f"结构化输出：need_reply={str(result['need_reply']).lower()}，content={result['content']}")
        if result["need_reply"]:
            self.workflow.send_current_chat_message(result["content"], self._emit_progress)
            self.message_recorded.emit({"handle":self.contact_handle,"display_name":record["sender"],"direction":"outbound","message_kind":"model_reply","content":result["content"]})
        else:
            self._emit_progress("MODEL_NO_REPLY", "模型判断无需回复，返回首页继续监听")


class InboxListenWorker(QThread):
    progress_changed = Signal(str, str, int)
    failed = Signal(str)
    cancelled = Signal(str)
    message_recorded = Signal(object)
    messages_updated = Signal()

    def __init__(self, adb: ADBClient, serial: str, runtime: dict,
                 conversation_repository: ConversationRepository | None = None,
                 job_id: int | None = None) -> None:
        super().__init__()
        self.adb = adb
        self.serial = serial
        self.runtime = runtime
        self.conversation_repository = conversation_repository
        self.job_id = job_id
        self.workflow: TikTokInboxListener | None = None

    def stop(self) -> None:
        if self.workflow:
            self.workflow.cancel()

    def run(self) -> None:
        try:
            self.workflow = TikTokInboxListener(self.adb, self.serial)
            while True:
                record = self.workflow.listen_once(
                    lambda step, text, percent: self.progress_changed.emit(
                        step, text, percent
                    ),
                    timeout=None,
                )
                if record:
                    messages = _record_messages(record)
                    if self.conversation_repository is not None:
                        messages = self.conversation_repository.record_incoming_batch(
                            display_name=record["sender"],
                            messages=messages,
                            job_id=self.job_id,
                        )
                        if messages:
                            self.messages_updated.emit()
                    else:
                        for item in messages:
                            self.message_recorded.emit({"handle":"","display_name":record["sender"],"direction":"inbound","message_kind":"received_"+item.get("type","text"),"content":item["content"]})
                    if not messages:
                        self.progress_changed.emit("MESSAGE_DUPLICATE", "本轮消息均已记录，返回首页继续监听", 100)
                        self.workflow._return_to_tiktok_home()
                        continue
                    user_message = _strategy_message(messages)
                    self.progress_changed.emit("MODEL_ANALYZE", f"正在使用策略“{self.runtime['strategy']['name']}”分析 {len(messages)} 条新消息：{user_message}", 100)
                    result = MessageStrategyService().evaluate(
                        self.runtime["strategy"], self.runtime["model"], self.runtime["provider"],
                        user_message, self.runtime["proxy"],
                        cancelled=lambda: self.workflow.cancel_event.is_set(),
                    )
                    self.progress_changed.emit("MODEL_DECISION", f"结构化输出：need_reply={str(result['need_reply']).lower()}，content={result['content']}", 100)
                    if result["need_reply"]:
                        self.workflow.send_current_chat_message(
                            result["content"],
                            lambda step,text,percent: self.progress_changed.emit(step,text,percent),
                        )
                        self.message_recorded.emit({"handle":"","display_name":record["sender"],"direction":"outbound","message_kind":"model_reply","content":result["content"]})
                    else:
                        self.progress_changed.emit("MODEL_NO_REPLY", "模型判断无需回复，返回首页继续监听", 100)
                self.progress_changed.emit(
                    "LISTEN_CONTINUE", "本条消息读取完成，返回首页继续监听", 100
                )
        except WorkflowCancelled as error:
            self.cancelled.emit(str(error))
        except Exception as error:
            self.failed.emit(str(error))


class AutomationTasksWidget(QWidget):
    messages_updated = Signal()
    DEBUG_DEFAULT_HANDLE = "@thu.hoi8443"

    def __init__(
        self,
        repository: AutomationJobRepository,
        user_repository: UserRepository,
        settings_repository: SettingsRepository,
        adb_client: ADBClient | None = None,
        strategy_repository: MessageStrategyRepository | None = None,
        conversation_repository: ConversationRepository | None = None,
    ) -> None:
        super().__init__()
        self.repository = repository
        self.user_repository = user_repository
        self.settings_repository = settings_repository
        self.adb_client = adb_client
        self.strategy_repository = strategy_repository
        self.conversation_repository = conversation_repository
        self.ready_devices: dict[str, AndroidDevice] = {}
        self._has_tags = False
        self._has_devices = False
        self.debug_worker: MessageDebugWorker | None = None
        self.debug_job_id: int | None = None
        self.listen_worker: InboxListenWorker | None = None
        self.listen_job_id: int | None = None
        self.listen_stopping = False
        self._build_ui()
        self.refresh_devices()
        self.refresh_tags()
        self.refresh_strategies()
        self.refresh_jobs()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 28)
        layout.setSpacing(18)

        header = QHBoxLayout()
        header.addWidget(SectionHeader(
            "自动化任务",
            "配置按用户标签执行的对话任务；同一时间只能启动一个任务",
        ))
        header.addStretch()
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh_all)
        header.addWidget(refresh)
        layout.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        list_card, list_layout = card_layout()
        list_layout.addWidget(label("任务列表", "SectionTitle"))
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels([
            "类型", "执行设备", "模型策略", "用户标签", "启动话术", "执行次数", "已完成", "状态"
        ])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        table_header = self.table.horizontalHeader()
        table_header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table_header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.setMinimumHeight(280)
        list_layout.addWidget(self.table)

        controls = QHBoxLayout()
        self.start_button = QPushButton("启动选中任务")
        self.start_button.setObjectName("Primary")
        self.start_button.clicked.connect(self.start_selected)
        self.stop_button = QPushButton("停止")
        self.stop_button.clicked.connect(self.stop_selected)
        self.delete_button = QPushButton("删除")
        self.delete_button.setObjectName("DangerButton")
        self.delete_button.clicked.connect(self.delete_selected)
        controls.addWidget(self.start_button)
        controls.addWidget(self.stop_button)
        controls.addWidget(self.delete_button)
        controls.addStretch()
        controls.addWidget(label("指定用户", "Muted"))
        self.debug_handle = QLineEdit()
        self.debug_handle.setPlaceholderText("@用户名")
        self.debug_handle.setText(self.DEBUG_DEFAULT_HANDLE)
        self.debug_handle.setClearButtonEnabled(True)
        self.debug_handle.setFixedWidth(160)
        self.debug_handle.returnPressed.connect(self.debug_selected)
        controls.addWidget(self.debug_handle)
        self.debug_button = QPushButton("调试任务")
        self.debug_button.clicked.connect(self.toggle_debug)
        controls.addWidget(self.debug_button)
        list_layout.addLayout(controls)
        splitter.addWidget(list_card)

        form_card, form_layout = card_layout()
        form_layout.addWidget(label("新增自动化任务", "SectionTitle"))
        form = QFormLayout()
        form.setSpacing(12)
        self.type_combo = QComboBox()
        self.type_combo.addItem("对话", "dialog")
        self.device_combo = QComboBox()
        self.strategy_combo = QComboBox()
        self.tag_combo = QComboBox()
        self.opening_message = QPlainTextEdit()
        self.opening_message.setPlaceholderText(
            "输入针对该标签用户首次发起对话时使用的话术"
        )
        self.opening_message.setMinimumHeight(110)
        self.execution_count = QSpinBox()
        self.execution_count.setRange(1, 10000)
        self.execution_count.setValue(1)
        self.execution_count.setSuffix(" 次")
        form.addRow("自动化任务类型", self.type_combo)
        form.addRow("执行设备", self.device_combo)
        form.addRow("模型策略", self.strategy_combo)
        form.addRow("目标用户标签", self.tag_combo)
        form.addRow("启动话术", self.opening_message)
        form.addRow("执行次数", self.execution_count)
        form_layout.addLayout(form)
        self.create_button = QPushButton("保存自动化任务")
        self.create_button.setObjectName("Primary")
        self.create_button.clicked.connect(self.create_job)
        form_layout.addWidget(self.create_button)
        form_layout.addStretch()
        splitter.addWidget(form_card)
        splitter.setSizes([780, 420])
        layout.addWidget(splitter)

        log_card, log_layout = card_layout()
        log_layout.addWidget(label("执行日志", "SectionTitle"))
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("选择自动化任务后显示持久化执行日志")
        self.log_view.setMaximumBlockCount(1000)
        self.log_view.setMinimumHeight(170)
        log_layout.addWidget(self.log_view)
        layout.addWidget(log_card)

    def refresh_all(self) -> None:
        self.refresh_devices()
        self.refresh_tags()
        self.refresh_strategies()
        self.refresh_jobs(self.selected_job_id())

    def refresh_devices(self) -> None:
        current = str(self.device_combo.currentData() or "")
        self.device_combo.clear()
        devices = list(self.ready_devices.values())
        for device in devices:
            self.device_combo.addItem(
                f"{device.display_name} · {device.serial}", device.serial
            )
        self._has_devices = bool(devices)
        if not devices:
            self.device_combo.addItem("等待设备扫描或暂无已就绪设备", "")
        index = self.device_combo.findData(current)
        if index >= 0:
            self.device_combo.setCurrentIndex(index)
        self._update_create_enabled()

    def update_devices(self, devices: list[AndroidDevice]) -> None:
        """Use the detailed readiness result produced by Device Management."""
        self.ready_devices = {
            item.serial: item for item in devices if item.automation_ready
        }
        self.refresh_devices()

    def refresh_tags(self) -> None:
        current = str(self.tag_combo.currentData() or "")
        tags = self.user_repository.list_tags()
        self.tag_combo.clear()
        if tags:
            for tag in tags:
                self.tag_combo.addItem(tag, tag)
            index = self.tag_combo.findData(current)
            if index >= 0:
                self.tag_combo.setCurrentIndex(index)
            self._has_tags = True
        else:
            self.tag_combo.addItem("暂无用户标签", "")
            self._has_tags = False
        self._update_create_enabled()

    def refresh_strategies(self) -> None:
        current = self.strategy_combo.currentData()
        self.strategy_combo.clear()
        rows = self.strategy_repository.list() if self.strategy_repository else []
        if not rows:
            self.strategy_combo.addItem("暂无策略，请先到消息策略中新建", 0)
            return
        for item in rows:
            self.strategy_combo.addItem(item["name"], item["id"])
        index = self.strategy_combo.findData(current)
        if index >= 0:
            self.strategy_combo.setCurrentIndex(index)

    def _update_create_enabled(self) -> None:
        if hasattr(self, "create_button"):
            self.create_button.setEnabled(self._has_tags and self._has_devices)

    def create_job(self) -> None:
        if int(self.strategy_combo.currentData() or 0) <= 0:
            QMessageBox.warning(self, "无法创建自动化任务", "模型策略列表为空，请先在“消息策略”中建立一个策略。")
            return
        try:
            job_id = self.repository.create_job({
                "job_type": self.type_combo.currentData(),
                "device_serial": self.device_combo.currentData(),
                "device_name": self.device_combo.currentText(),
                "strategy_id": self.strategy_combo.currentData(),
                "strategy_name": self.strategy_combo.currentText(),
                "user_tag": self.tag_combo.currentData(),
                "opening_message": self.opening_message.toPlainText(),
                "execution_count": self.execution_count.value(),
            })
        except Exception as error:
            QMessageBox.warning(self, "无法创建自动化任务", str(error))
            return
        self.opening_message.clear()
        self.execution_count.setValue(1)
        self.refresh_jobs(job_id)

    def refresh_jobs(self, selected_job_id: int | None = None) -> None:
        if selected_job_id is None:
            selected_job_id = self.selected_job_id()
        jobs = self.repository.list_jobs()
        self.table.setRowCount(len(jobs))
        for row, job in enumerate(jobs):
            values = [
                "对话" if job["job_type"] == "dialog" else job["job_type"],
                job["device_name"] or job["device_serial"],
                job["strategy_name"] or f'策略 #{job["strategy_id"]}',
                job["user_tag"],
                job["opening_message"],
                job["execution_count"],
                job["completed_count"],
                STATUS_LABELS.get(job["status"], job["status"]),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, int(job["id"]))
                item.setToolTip(str(value))
                self.table.setItem(row, column, item)
            if int(job["id"]) == selected_job_id:
                self.table.selectRow(row)
        if jobs and not self.table.selectedItems():
            self.table.selectRow(0)
        self._selection_changed()

    def selected_job_id(self) -> int | None:
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        value = item.data(Qt.ItemDataRole.UserRole) if item else None
        return int(value) if value is not None else None

    def _selection_changed(self) -> None:
        job_id = self.selected_job_id()
        job = self.repository.get_job(job_id) if job_id is not None else None
        if not job:
            self.log_view.clear()
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(False)
            self.delete_button.setEnabled(False)
            self.debug_button.setEnabled(False)
            return
        timezone_name = self.settings_repository.get_timezone()
        logs = self.repository.list_logs(job_id)
        self.log_view.setPlainText("\n".join(
            f'[{format_utc_timestamp(item["created_at"], timezone_name)}] '
            f'{item["level"]:<5} {item["message"]}'
            for item in logs
        ))
        self.log_view.verticalScrollBar().setValue(
            self.log_view.verticalScrollBar().maximum()
        )
        running = job["status"] == "running"
        automating = self.debug_worker is not None or self.listen_worker is not None
        self.start_button.setEnabled(not running and not automating)
        listening = self.listen_worker is not None and self.listen_job_id == job_id
        self.stop_button.setEnabled((running or listening) and not self.listen_stopping)
        self.delete_button.setEnabled(not running and not automating)
        self.debug_handle.setEnabled(not automating)
        self.debug_button.setEnabled(
            self.listen_worker is None
            and (
                self.debug_worker is None
                or self.debug_job_id == job_id
            )
            and not self.listen_stopping
        )

    def toggle_debug(self) -> None:
        if self.debug_worker is not None:
            if self.debug_job_id != self.selected_job_id():
                return
            job_id = int(self.debug_job_id)
            self.repository.add_log(job_id, "正在停止调试任务持续监听……")
            self.listen_stopping = True
            self.debug_button.setEnabled(False)
            self.debug_button.setText("停止中…")
            self.debug_worker.stop()
            return
        self.debug_selected()

    def debug_selected(self) -> None:
        if self.debug_worker is not None or self.listen_worker is not None:
            return
        job_id = self.selected_job_id()
        if job_id is None:
            QMessageBox.information(self, "调试任务", "请先选择一个自动化任务。")
            return
        if self.adb_client is None:
            QMessageBox.warning(self, "无法调试任务", "ADB 客户端不可用。")
            return
        job = self.repository.get_job(job_id)
        if not job:
            QMessageBox.warning(self, "无法调试任务", "自动化任务不存在。")
            return
        serial = str(job["device_serial"] or "")
        available = set(self.ready_devices)
        if serial not in available:
            QMessageBox.warning(self, "无法调试任务", "任务所选设备当前未连接或未授权。")
            return
        handle = self.debug_handle.text().strip() or self.DEBUG_DEFAULT_HANDLE
        message = str(job["opening_message"] or "").strip()
        if not message:
            QMessageBox.warning(self, "无法调试任务", "选中任务的启动话术为空。")
            return
        try:
            runtime = self._strategy_runtime(job)
        except Exception as error:
            QMessageBox.warning(self, "无法调试任务", str(error)); return
        self.repository.add_log(
            job_id,
            f"[真实调试] 准备在设备 {serial} 向 {handle} 发送消息：{message}",
        )
        self.debug_worker = MessageDebugWorker(
            self.adb_client, serial, handle, message, runtime,
            self.conversation_repository, job_id,
        )
        self.debug_worker.progress_changed.connect(
            lambda step, text, percent: self._debug_progress(
                job_id, step, text, percent
            )
        )
        self.debug_worker.succeeded.connect(
            lambda normalized, sent_message: self._debug_succeeded(
                job_id, normalized, sent_message
            )
        )
        self.debug_worker.failed.connect(
            lambda error: self._debug_failed(job_id, error)
        )
        self.debug_worker.cancelled.connect(
            lambda text: self._debug_cancelled(job_id, text)
        )
        self.debug_worker.message_recorded.connect(
            lambda values: self._record_message(job_id, values)
        )
        self.debug_worker.messages_updated.connect(self.messages_updated.emit)
        self.debug_worker.finished.connect(
            lambda: self._debug_finished(job_id)
        )
        self.debug_button.setEnabled(False)
        self._set_debug_button_running(True)
        self.start_button.setEnabled(False)
        self.debug_job_id = job_id
        self.listen_stopping = False
        self.debug_worker.start()
        self.refresh_jobs(job_id)

    def _debug_progress(
        self, job_id: int, step: str, message: str, percent: int
    ) -> None:
        self.repository.add_log(job_id, f"[{step}] {message}（{percent}%）")
        if self.selected_job_id() == job_id:
            self._selection_changed()

    def _debug_succeeded(self, job_id: int, handle: str, message: str) -> None:
        self.debug_handle.setText(handle)
        self.repository.add_log(
            job_id,
            f"[真实调试成功] 已向 {handle} 发送消息：{message}；"
            "未修改用户首次消息状态",
        )
        self.refresh_jobs(job_id)

    def _debug_failed(self, job_id: int, error: str) -> None:
        self.repository.add_log(job_id, f"[真实调试失败] {error}", "ERROR")
        self.refresh_jobs(job_id)
        QMessageBox.warning(self, "调试任务失败", error)

    def _debug_cancelled(self, job_id: int, message: str) -> None:
        self.repository.add_log(job_id, f"调试任务监听已停止：{message}")
        self.refresh_jobs(job_id)

    def _debug_finished(self, job_id: int) -> None:
        worker = self.debug_worker
        self.debug_worker = None
        self.debug_job_id = None
        self.listen_stopping = False
        if worker:
            worker.deleteLater()
        self._set_debug_button_running(False)
        self.refresh_jobs(job_id)

    def _set_debug_button_running(self, running: bool) -> None:
        self.debug_button.setText("停止调试" if running else "调试任务")
        self.debug_button.setObjectName("DebugStopButton" if running else "")
        self.debug_button.style().unpolish(self.debug_button)
        self.debug_button.style().polish(self.debug_button)

    def start_selected(self) -> None:
        if self.debug_worker is not None or self.listen_worker is not None:
            return
        job_id = self.selected_job_id()
        if job_id is None:
            QMessageBox.information(self, "启动任务", "请先选择一个自动化任务。")
            return
        try:
            self.repository.start_job(job_id)
            self.repository.execute_dialog_preview(job_id, keep_running=True)
        except Exception as error:
            job = self.repository.get_job(job_id)
            if job and job["status"] == "running":
                self.repository.fail_job(job_id, str(error))
            QMessageBox.warning(self, "无法启动任务", str(error))
            return
        job = self.repository.get_job(job_id)
        if job and self.adb_client is not None:
            self._start_inbox_listener(job_id, str(job["device_serial"] or ""))
        self.refresh_jobs(job_id)

    def _start_inbox_listener(self, job_id: int, serial: str) -> None:
        if not serial or self.adb_client is None:
            self.repository.fail_job(job_id, "无法启动消息监听：执行设备不可用")
            return
        available = set(self.ready_devices)
        if serial not in available:
            self.repository.fail_job(job_id, "无法启动消息监听：设备未连接或未授权")
            return
        self.repository.add_log(job_id, "发送流程结束，开始监听收件箱未读消息")
        job = self.repository.get_job(job_id)
        try:
            runtime = self._strategy_runtime(job or {})
        except Exception as error:
            self.repository.fail_job(job_id, f"模型策略不可用：{error}"); return
        self.listen_worker = InboxListenWorker(
            self.adb_client, serial, runtime, self.conversation_repository, job_id
        )
        self.listen_worker.progress_changed.connect(
            lambda step, text, percent: self._listen_progress(
                job_id, step, text, percent
            )
        )
        self.listen_worker.failed.connect(
            lambda error: self._listen_failed(job_id, error)
        )
        self.listen_worker.cancelled.connect(
            lambda message: self._listen_cancelled(job_id, message)
        )
        self.listen_worker.message_recorded.connect(
            lambda values: self._record_message(job_id, values)
        )
        self.listen_worker.messages_updated.connect(self.messages_updated.emit)
        self.listen_worker.finished.connect(
            lambda: self._listen_finished(job_id)
        )
        self.listen_job_id = job_id
        self.listen_stopping = False
        self.listen_worker.start()

    def _listen_progress(
        self, job_id: int, step: str, message: str, percent: int
    ) -> None:
        self.repository.add_log(job_id, f"[{step}] {message}")
        if self.selected_job_id() == job_id:
            self._selection_changed()

    def _listen_failed(self, job_id: int, error: str) -> None:
        self.repository.add_log(job_id, f"[消息监听失败] {error}", "ERROR")
        job = self.repository.get_job(job_id)
        if job and job["status"] == "running":
            self.repository.fail_job(job_id, error)
        self.refresh_jobs(job_id)

    def _listen_cancelled(self, job_id: int, message: str) -> None:
        job = self.repository.get_job(job_id)
        if job and job["status"] == "running":
            self.repository.stop_job(job_id)
        self.repository.add_log(job_id, f"消息监听已由用户终止：{message}")
        self.refresh_jobs(job_id)

    def _listen_finished(self, job_id: int) -> None:
        worker = self.listen_worker
        self.listen_worker = None
        self.listen_job_id = None
        self.listen_stopping = False
        if worker:
            worker.deleteLater()
        self.refresh_jobs(job_id)

    def stop_selected(self) -> None:
        job_id = self.selected_job_id()
        if job_id is None:
            return
        if self.listen_worker is not None and self.listen_job_id == job_id:
            self.repository.add_log(job_id, "正在停止持续消息监听……")
            self.listen_stopping = True
            self.stop_button.setEnabled(False)
            self.listen_worker.stop()
            return
        try:
            self.repository.stop_job(job_id)
        except Exception as error:
            QMessageBox.warning(self, "无法停止任务", str(error))
            return
        self.refresh_jobs(job_id)

    def _strategy_runtime(self, job: dict) -> dict:
        if self.strategy_repository is None:
            raise ValueError("消息策略仓库不可用")
        strategy = self.strategy_repository.get(int(job.get("strategy_id") or 0))
        if not strategy: raise ValueError("任务绑定的消息策略不存在")
        model = self.settings_repository.get_model(int(strategy["model_id"]))
        if not model or model["model_type"] != "llm" or not model["enabled"]:
            raise ValueError("策略绑定的大语言模型不可用")
        provider = self.settings_repository.get_provider(int(model["provider_id"]), reveal_key=True)
        if not provider or not provider["enabled"]: raise ValueError("模型所属API厂家不可用")
        return {"strategy":strategy,"model":model,"provider":provider,
                "proxy":self.settings_repository.get_proxy_settings(reveal_password=True)}

    def _record_message(self, job_id: int, values: dict) -> None:
        if self.conversation_repository is None:
            self.repository.add_log(job_id,"消息已处理，但消息数据库不可用","ERROR"); return
        payload=dict(values); payload["job_id"]=job_id
        try:self.conversation_repository.record(payload)
        except Exception as error:self.repository.add_log(job_id,f"消息入库失败：{error}","ERROR"); return
        self.messages_updated.emit()

    def shutdown(self) -> None:
        for worker in (self.debug_worker, self.listen_worker):
            if worker is not None:
                worker.stop()
                worker.wait(5_000)

    def delete_selected(self) -> None:
        job_id = self.selected_job_id()
        if job_id is None:
            return
        answer = QMessageBox.question(
            self, "删除自动化任务", "确定删除选中任务及其全部执行日志吗？"
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.repository.delete_job(job_id)
        except Exception as error:
            QMessageBox.warning(self, "无法删除任务", str(error))
            return
        self.refresh_jobs()
