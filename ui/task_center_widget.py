"""Database-backed task center for the first TikTok automation stage."""

from __future__ import annotations

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from automation import TikTokSearchWorkflow, WorkflowCancelled
from devices import ADBClient, AndroidDevice
from storage import TaskRepository, UserRepository
from ui.widgets import SectionHeader, card_layout, label


STATUS_LABELS = {
    "created": "待执行",
    "running": "执行中",
    "completed": "已完成",
    "failed": "失败",
    "cancelled": "已停止",
}

STEP_LABELS = {
    "CREATED": "已创建",
    "START_APP": "启动 TikTok",
    "OPEN_SEARCH": "打开搜索",
    "ENTER_KEYWORD": "输入关键词",
    "WAIT_RESULTS": "加载结果",
    "SELECT_CONTENT": "选择内容",
    "OPEN_CONTENT": "进入内容",
    "CHAT_OPENED": "已进入聊天",
    "COLLECT_COMMENTS": "采集评论",
    "COMMENT_COLLECTED": "采集结果",
    "COMMENTS_COLLECTED": "采集完成",
    "ROOM_STARTED": "进入房间",
    "ROOM_COMPLETED": "房间完成",
    "NEXT_ROOM": "切换房间",
    "COLLECTION_FINISHED": "采集结束",
    "FAILED": "执行失败",
    "CANCELLED": "已停止",
}


class TaskExecutionWorker(QThread):
    progress_changed = Signal(int, str, str, int)
    succeeded = Signal(int, str)
    failed = Signal(int, str)
    cancelled = Signal(int, str)
    user_collected = Signal(int, object)

    def __init__(self, task: dict, adb: ADBClient) -> None:
        super().__init__()
        self.task = task
        self.adb = adb
        self.workflow: TikTokSearchWorkflow | None = None

    def stop(self) -> None:
        if self.workflow:
            self.workflow.cancel()

    def run(self) -> None:
        task_id = int(self.task["id"])
        try:
            keywords = self.task.get("keywords") or []
            if not keywords:
                raise ValueError("任务没有搜索关键词")
            keyword = str(keywords[0]).strip()
            self.workflow = TikTokSearchWorkflow(
                self.adb,
                self.task["device_serial"],
                self.task["app_package"],
            )
            chosen_type = self.workflow.run(
                keyword,
                self.task["content_type"],
                lambda step, message, percent: self.progress_changed.emit(
                    task_id, step, message, percent
                ),
                max_comments=int(self.task.get("max_comments", 20)),
                collection_minutes=int(self.task.get("collection_minutes", 2)),
                user_callback=lambda record: self.user_collected.emit(task_id, record),
            )
            self.succeeded.emit(task_id, chosen_type)
        except WorkflowCancelled as error:
            self.cancelled.emit(task_id, str(error))
        except Exception as error:
            self.failed.emit(task_id, str(error))


class TaskCenterWidget(QWidget):
    """Create, persist, execute and inspect real automation tasks."""

    users_updated = Signal()

    def __init__(
        self,
        adb: ADBClient,
        repository: TaskRepository,
        user_repository: UserRepository,
    ) -> None:
        super().__init__()
        self.adb = adb
        self.repository = repository
        self.user_repository = user_repository
        self.devices: dict[str, AndroidDevice] = {}
        self.workers: dict[int, TaskExecutionWorker] = {}
        self._build_ui()
        self.refresh_tasks()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 28)
        layout.setSpacing(18)

        header = QHBoxLayout()
        header.addWidget(SectionHeader(
            "任务中心",
            "创建真实 TikTok 搜索任务：进入视频评论区或直播聊天区后停止",
        ))
        header.addStretch()
        self.refresh_button = QPushButton("刷新任务")
        self.refresh_button.clicked.connect(self.refresh_tasks)
        header.addWidget(self.refresh_button)
        layout.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        list_card, list_layout = card_layout()
        list_header = QHBoxLayout()
        list_header.addWidget(label("任务列表", "SectionTitle"))
        list_header.addStretch()
        self.device_hint = label("等待设备扫描", "Small")
        list_header.addWidget(self.device_hint)
        list_layout.addLayout(list_header)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([
            "任务名称", "设备", "关键词", "内容", "当前步骤", "进度", "状态"
        ])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.setMinimumHeight(280)
        list_layout.addWidget(self.table)

        controls = QHBoxLayout()
        self.start_button = QPushButton("执行选中任务")
        self.start_button.setObjectName("Primary")
        self.start_button.clicked.connect(self.start_selected_task)
        self.stop_button = QPushButton("停止")
        self.stop_button.clicked.connect(self.stop_selected_task)
        self.delete_button = QPushButton("删除")
        self.delete_button.clicked.connect(self.delete_selected_task)
        controls.addWidget(self.start_button)
        controls.addWidget(self.stop_button)
        controls.addWidget(self.delete_button)
        controls.addStretch()
        list_layout.addLayout(controls)

        self.current_progress = QProgressBar()
        self.current_progress.setRange(0, 100)
        self.current_progress.setValue(0)
        list_layout.addWidget(self.current_progress)
        splitter.addWidget(list_card)

        form_card, form_layout = card_layout()
        form_layout.addWidget(label("新建 TikTok 任务", "SectionTitle"))
        form = QFormLayout()
        form.setSpacing(12)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例如：露营视频评论采集")
        self.device_combo = QComboBox()
        self.device_combo.addItem("暂无可用设备", "")
        self.keyword_edit = QLineEdit()
        self.keyword_edit.setPlaceholderText("输入一个搜索关键词")
        self.content_combo = QComboBox()
        self.content_combo.addItem("视频（进入评论区）", "video")
        self.content_combo.addItem("直播（进入聊天区）", "live")
        self.content_combo.addItem("自动选择可用内容", "either")
        self.max_comments_spin = QSpinBox()
        self.max_comments_spin.setRange(1, 200)
        self.max_comments_spin.setValue(20)
        self.max_comments_spin.setSuffix(" 条")
        self.collection_minutes_spin = QSpinBox()
        self.collection_minutes_spin.setRange(1, 1440)
        self.collection_minutes_spin.setValue(2)
        self.collection_minutes_spin.setSuffix(" 分钟")
        self.app_combo = QComboBox()
        self.app_combo.addItem("TikTok", "com.zhiliaoapp.musically")
        form.addRow("任务名称", self.name_edit)
        form.addRow("执行设备", self.device_combo)
        form.addRow("目标应用", self.app_combo)
        form.addRow("搜索关键词", self.keyword_edit)
        form.addRow("结果类型", self.content_combo)
        form.addRow("每个房间最大评论", self.max_comments_spin)
        form.addRow("采集时间", self.collection_minutes_spin)
        form_layout.addLayout(form)

        scope = label(
            "视频任务会采集用户名、@名字、关注数、粉丝数、赞数和留言并写入执行日志；"
            "达到单房间上限或该房间评论结束后，会在采集时间内继续下一个视频。"
            "直播任务暂时只进入聊天区域。不会关注用户，也不会发送消息。",
            "Muted",
        )
        scope.setWordWrap(True)
        form_layout.addWidget(scope)
        save_row = QHBoxLayout()
        self.save_button = QPushButton("保存任务")
        self.save_button.clicked.connect(lambda: self.create_task(False))
        self.save_run_button = QPushButton("保存并执行")
        self.save_run_button.setObjectName("Primary")
        self.save_run_button.clicked.connect(lambda: self.create_task(True))
        save_row.addWidget(self.save_button)
        save_row.addWidget(self.save_run_button)
        form_layout.addLayout(save_row)
        form_layout.addStretch()
        splitter.addWidget(form_card)
        splitter.setSizes([760, 430])
        layout.addWidget(splitter)

        log_card, log_layout = card_layout()
        log_layout.addWidget(label("执行日志", "SectionTitle"))
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("选择任务后显示持久化执行日志")
        self.log_view.setMaximumBlockCount(500)
        self.log_view.setMinimumHeight(150)
        log_layout.addWidget(self.log_view)
        layout.addWidget(log_card)

        self._selection_changed()

    def update_devices(self, devices: list[AndroidDevice]) -> None:
        current = self.device_combo.currentData()
        authorized = [item for item in devices if item.authorized]
        self.devices = {item.serial: item for item in authorized}
        self.device_combo.clear()
        if not authorized:
            self.device_combo.addItem("暂无已授权设备", "")
        else:
            for device in authorized:
                self.device_combo.addItem(
                    f"{device.display_name} · {device.serial}", device.serial
                )
            index = self.device_combo.findData(current)
            if index >= 0:
                self.device_combo.setCurrentIndex(index)
        self.device_hint.setText(f"{len(authorized)} 台设备可执行")
        self._selection_changed()

    def create_task(self, run_after_save: bool) -> None:
        serial = str(self.device_combo.currentData() or "")
        keyword = self.keyword_edit.text().strip()
        name = self.name_edit.text().strip() or (f"{keyword} TikTok 搜索" if keyword else "")
        try:
            task_id = self.repository.create_task({
                "name": name,
                "device_serial": serial,
                "app_package": self.app_combo.currentData(),
                "keywords": [keyword],
                "content_type": self.content_combo.currentData(),
                "max_comments": self.max_comments_spin.value(),
                "collection_minutes": self.collection_minutes_spin.value(),
            })
        except Exception as error:
            QMessageBox.warning(self, "无法创建任务", str(error))
            return
        self.name_edit.clear()
        self.keyword_edit.clear()
        self.refresh_tasks(select_task_id=task_id)
        if run_after_save:
            self.start_task(task_id)

    def refresh_tasks(self, select_task_id: int | None = None) -> None:
        if select_task_id is None:
            select_task_id = self.selected_task_id()
        tasks = self.repository.list_tasks()
        self.table.setRowCount(len(tasks))
        for row, task in enumerate(tasks):
            serial = task["device_serial"]
            device = self.devices.get(serial)
            device_text = device.display_name if device else serial
            content_text = {"video": "视频", "live": "直播", "either": "自动"}.get(
                task["content_type"], task["content_type"]
            )
            values = [
                task["name"],
                device_text,
                ", ".join(task["keywords"]),
                content_text,
                STEP_LABELS.get(task["current_step"], task["current_step"]),
                f'{task["progress"]}%',
                STATUS_LABELS.get(task["status"], task["status"]),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, int(task["id"]))
                self.table.setItem(row, column, item)
            if int(task["id"]) == select_task_id:
                self.table.selectRow(row)
        if self.table.rowCount() and not self.table.selectedItems():
            self.table.selectRow(0)
        self._selection_changed()

    def selected_task_id(self) -> int | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        value = item.data(Qt.ItemDataRole.UserRole) if item else None
        return int(value) if value is not None else None

    def _selection_changed(self) -> None:
        task_id = self.selected_task_id()
        task = self.repository.get_task(task_id) if task_id is not None else None
        if not task:
            self.log_view.clear()
            self.current_progress.setValue(0)
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(False)
            self.delete_button.setEnabled(False)
            return
        logs = self.repository.list_logs(task_id)
        self.log_view.setPlainText("\n".join(
            f'[{item["created_at"]}] {item["level"]:<5} '
            f'{STEP_LABELS.get(item["step"], item["step"])}  {item["message"]}'
            for item in logs
        ))
        self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())
        self.current_progress.setValue(int(task["progress"]))
        running = task_id in self.workers and self.workers[task_id].isRunning()
        serial_busy = any(
            worker.isRunning() and worker.task["device_serial"] == task["device_serial"]
            for worker in self.workers.values()
        )
        device_ready = task["device_serial"] in self.devices
        self.start_button.setEnabled(not running and not serial_busy and device_ready)
        self.stop_button.setEnabled(running)
        self.delete_button.setEnabled(not running)

    def start_selected_task(self) -> None:
        task_id = self.selected_task_id()
        if task_id is not None:
            self.start_task(task_id)

    def start_task(self, task_id: int) -> None:
        task = self.repository.get_task(task_id)
        if not task:
            return
        serial = task["device_serial"]
        if serial not in self.devices:
            QMessageBox.warning(self, "设备不可用", "任务设备当前未连接或未授权。")
            return
        if any(
            worker.isRunning() and worker.task["device_serial"] == serial
            for worker in self.workers.values()
        ):
            QMessageBox.information(self, "设备忙碌", "同一台设备同时只能执行一个任务。")
            return
        self.repository.update_runtime(
            task_id, status="running", step="START_APP", progress=0,
            keyword=task["keywords"][0], error="",
        )
        self.repository.add_log(task_id, "INFO", "START_APP", "任务开始执行")
        worker = TaskExecutionWorker(task, self.adb)
        worker.progress_changed.connect(self._task_progress)
        worker.succeeded.connect(self._task_succeeded)
        worker.failed.connect(self._task_failed)
        worker.cancelled.connect(self._task_cancelled)
        worker.user_collected.connect(self._user_collected)
        worker.finished.connect(lambda task_id=task_id: self._worker_finished(task_id))
        self.workers[task_id] = worker
        self.refresh_tasks(select_task_id=task_id)
        worker.start()

    def stop_selected_task(self) -> None:
        task_id = self.selected_task_id()
        worker = self.workers.get(task_id) if task_id is not None else None
        if worker and worker.isRunning():
            self.repository.add_log(task_id, "INFO", "CANCELLED", "正在请求停止任务")
            worker.stop()
            self._selection_changed()

    def delete_selected_task(self) -> None:
        task_id = self.selected_task_id()
        if task_id is None:
            return
        answer = QMessageBox.question(self, "删除任务", "确定删除选中任务及其全部日志吗？")
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.repository.delete_task(task_id)
        except Exception as error:
            QMessageBox.warning(self, "无法删除任务", str(error))
            return
        self.refresh_tasks()

    def _task_progress(self, task_id: int, step: str, message: str, percent: int) -> None:
        task = self.repository.get_task(task_id)
        if step == "COMMENT_COLLECTED" or not task or task["current_step"] != step:
            self.repository.add_log(task_id, "INFO", step, message)
        self.repository.update_runtime(task_id, status="running", step=step, progress=percent)
        self.refresh_tasks(select_task_id=task_id)

    def _user_collected(self, task_id: int, record: dict) -> None:
        values = dict(record)
        values["task_id"] = task_id
        self.user_repository.upsert_collected_user(values)
        self.users_updated.emit()

    def _task_succeeded(self, task_id: int, chosen_type: str) -> None:
        type_text = "直播" if chosen_type == "live" else "视频"
        if chosen_type == "video":
            message = "视频房间轮询采集完成，用户信息与留言已写入执行日志"
            final_step = "COLLECTION_FINISHED"
        else:
            message = f"第一阶段完成：已进入{type_text}聊天区域，未发送任何消息"
            final_step = "CHAT_OPENED"
        self.repository.update_runtime(
            task_id, status="completed", step=final_step, progress=100, error=""
        )
        self.repository.add_log(task_id, "INFO", final_step, message)
        self.refresh_tasks(select_task_id=task_id)

    def _task_failed(self, task_id: int, message: str) -> None:
        self.repository.update_runtime(
            task_id, status="failed", step="FAILED", error=message
        )
        self.repository.add_log(task_id, "ERROR", "FAILED", message)
        self.refresh_tasks(select_task_id=task_id)

    def _task_cancelled(self, task_id: int, message: str) -> None:
        self.repository.update_runtime(
            task_id, status="cancelled", step="CANCELLED", error=""
        )
        self.repository.add_log(task_id, "INFO", "CANCELLED", message)
        self.refresh_tasks(select_task_id=task_id)

    def _worker_finished(self, task_id: int) -> None:
        worker = self.workers.pop(task_id, None)
        if worker:
            worker.deleteLater()
        self.refresh_tasks(select_task_id=task_id)

    def shutdown(self) -> None:
        for task_id, worker in self.workers.items():
            if worker.isRunning():
                self.repository.update_runtime(
                    task_id, status="cancelled", step="CANCELLED", error=""
                )
                self.repository.add_log(
                    task_id, "INFO", "CANCELLED", "应用退出，任务已停止"
                )
            worker.stop()
