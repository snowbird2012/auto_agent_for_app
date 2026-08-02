"""Interactive, streaming model test console."""

from __future__ import annotations

from time import perf_counter

import requests
from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from services.model_test_client import ModelTestClient, ModelTestError
from storage.settings_repository import SettingsRepository
from ui.ai_constants import TYPE_LABELS
from ui.widgets import card_layout, label


class ModelTestWorker(QThread):
    chunk = Signal(str)
    succeeded = Signal(float, int)
    failed = Signal(str)

    def __init__(self, provider: dict, model: dict, prompt: str, system_prompt: str, stream: bool, proxy_settings: dict) -> None:
        super().__init__()
        self.provider = provider
        self.model = model
        self.prompt = prompt
        self.system_prompt = system_prompt
        self.stream = stream
        self.proxy_settings = proxy_settings
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        started = perf_counter()
        total_characters = 0
        try:
            client = ModelTestClient()
            for text in client.stream_test(
                self.provider,
                self.model,
                self.prompt,
                self.system_prompt,
                self.stream,
                cancelled=lambda: self._cancelled,
                proxy_settings=self.proxy_settings,
            ):
                if self._cancelled:
                    break
                total_characters += len(text)
                self.chunk.emit(text)
            if self._cancelled:
                self.failed.emit("请求已停止")
            else:
                self.succeeded.emit(perf_counter() - started, total_characters)
        except (ModelTestError, requests.RequestException, ValueError) as error:
            self.failed.emit(str(error))
        except Exception as error:  # keep unexpected provider payloads visible in the console
            self.failed.emit(f"未预期错误：{error}")


class ModelTestWidget(QWidget):
    def __init__(self, repository: SettingsRepository) -> None:
        super().__init__()
        self.repository = repository
        self.worker: ModelTestWorker | None = None
        self.models: dict[int, dict] = {}
        self._build_ui()
        self.refresh_models()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 14, 0, 0)
        root.setSpacing(12)

        toolbar = QHBoxLayout()
        toolbar.addWidget(label("选择模型", "Muted"))
        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(420)
        self.model_combo.currentIndexChanged.connect(self._model_changed)
        toolbar.addWidget(self.model_combo)
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh_models)
        toolbar.addWidget(refresh)
        toolbar.addStretch()
        self.model_badge = label("未选择模型", "PillBlue")
        toolbar.addWidget(self.model_badge)
        root.addLayout(toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        request_card, request_layout = card_layout()
        request_layout.addWidget(label("测试请求", "SectionTitle"))
        request_layout.addWidget(label("系统提示词（可选）", "Muted"))
        self.system_prompt = QPlainTextEdit("你是一个简洁、友好的助手。")
        self.system_prompt.setMaximumHeight(82)
        request_layout.addWidget(self.system_prompt)
        request_layout.addWidget(label("消息内容", "Muted"))
        self.prompt = QPlainTextEdit()
        self.prompt.setPlaceholderText("输入要发送给模型的消息……")
        self.prompt.setPlainText("你好，请用一句话介绍你的能力。")
        request_layout.addWidget(self.prompt, 1)
        options = QHBoxLayout()
        self.stream_check = QCheckBox("流式输出")
        self.stream_check.setChecked(True)
        options.addWidget(self.stream_check)
        options.addStretch()
        clear = QPushButton("清空结果")
        clear.clicked.connect(self._clear_output)
        options.addWidget(clear)
        request_layout.addLayout(options)
        actions = QHBoxLayout()
        self.stop_button = QPushButton("停止")
        self.stop_button.setObjectName("DangerButton")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_test)
        actions.addWidget(self.stop_button)
        actions.addStretch()
        self.send_button = QPushButton("发送测试")
        self.send_button.setObjectName("Primary")
        self.send_button.clicked.connect(self.start_test)
        actions.addWidget(self.send_button)
        request_layout.addLayout(actions)
        splitter.addWidget(request_card)

        response_card, response_layout = card_layout()
        header = QHBoxLayout()
        header.addWidget(label("模型返回", "SectionTitle"))
        header.addStretch()
        self.status = label("就绪", "PillBlue")
        header.addWidget(self.status)
        response_layout.addLayout(header)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("发送后，返回内容会实时显示在这里。")
        response_layout.addWidget(self.output, 1)
        self.statistics = label("尚未发送请求", "Small")
        response_layout.addWidget(self.statistics)
        splitter.addWidget(response_card)
        splitter.setSizes([520, 680])
        splitter.setMinimumHeight(500)
        root.addWidget(splitter)

        note = label("说明：大语言和视觉模型支持流式文本；向量与排序模型返回结构化测试结果。测试请求不会写入聊天记录。", "Small")
        note.setWordWrap(True)
        root.addWidget(note)

    def refresh_models(self) -> None:
        selected = self.model_combo.currentData()
        models = self.repository.list_models()
        self.models = {model["id"]: model for model in models}
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for model in models:
            state = "" if model["enabled"] else " · 已停用"
            self.model_combo.addItem(
                f"{TYPE_LABELS[model['model_type']]} · {model['display_name']} · {model['provider_name']}{state}",
                model["id"],
            )
        index = self.model_combo.findData(selected)
        self.model_combo.setCurrentIndex(index if index >= 0 else (0 if models else -1))
        self.model_combo.blockSignals(False)
        self._model_changed()

    def _model_changed(self) -> None:
        model = self.models.get(self.model_combo.currentData())
        if not model:
            self.model_badge.setText("没有已注册模型")
            self.send_button.setEnabled(False)
            return
        self.send_button.setEnabled(self.worker is None)
        self.model_badge.setText(f"{TYPE_LABELS[model['model_type']]} · {model['model_id']}")
        is_chat = model["model_type"] in {"llm", "vision"}
        self.stream_check.setEnabled(is_chat)
        self.system_prompt.setEnabled(is_chat)
        if is_chat:
            self.stream_check.setChecked(True)
            self.prompt.setPlaceholderText("输入要发送给模型的消息……")
        elif model["model_type"] == "embedding":
            self.stream_check.setChecked(False)
            self.prompt.setPlaceholderText("输入需要转换为向量的文本……")
        else:
            self.stream_check.setChecked(False)
            self.prompt.setPlaceholderText("第一行输入查询，第二行起每行输入一篇候选文档……")

    def start_test(self) -> None:
        model = self.models.get(self.model_combo.currentData())
        if not model or self.worker is not None:
            return
        provider = self.repository.get_provider(model["provider_id"], reveal_key=True)
        if not provider:
            QMessageBox.warning(self, "无法测试", "模型所属的 API 厂家不存在。")
            return
        self.output.clear()
        self.statistics.setText("正在连接模型服务……")
        self.status.setText("连接中")
        self.status.setObjectName("PillOrange")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.send_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.model_combo.setEnabled(False)
        self.worker = ModelTestWorker(
            provider,
            model,
            self.prompt.toPlainText(),
            self.system_prompt.toPlainText(),
            self.stream_check.isChecked(),
            self.repository.get_proxy_settings(reveal_password=True),
        )
        self.worker.chunk.connect(self._append_chunk)
        self.worker.succeeded.connect(self._test_succeeded)
        self.worker.failed.connect(self._test_failed)
        self.worker.finished.connect(self._worker_finished)
        self.worker.start()

    def stop_test(self) -> None:
        if self.worker:
            self.status.setText("正在停止")
            self.worker.cancel()

    def _append_chunk(self, text: str) -> None:
        cursor = self.output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self.output.setTextCursor(cursor)
        self.output.ensureCursorVisible()
        self.status.setText("接收中")

    def _test_succeeded(self, elapsed: float, characters: int) -> None:
        self.status.setText("成功")
        self.status.setObjectName("PillGreen")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.statistics.setText(f"请求完成 · 耗时 {elapsed:.2f} 秒 · 返回 {characters} 个字符")

    def _test_failed(self, message: str) -> None:
        self.status.setText("失败")
        self.status.setObjectName("PillRed")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.statistics.setText(message)
        if self.output.toPlainText():
            self.output.appendPlainText(f"\n\n[错误] {message}")
        else:
            self.output.setPlainText(f"[错误] {message}")

    def _worker_finished(self) -> None:
        worker = self.worker
        self.worker = None
        self.send_button.setEnabled(bool(self.models))
        self.stop_button.setEnabled(False)
        self.model_combo.setEnabled(True)
        if worker:
            worker.deleteLater()

    def _clear_output(self) -> None:
        self.output.clear()
        self.statistics.setText("尚未发送请求")
        self.status.setText("就绪")
        self.status.setObjectName("PillBlue")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
