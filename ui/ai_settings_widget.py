"""AI provider and model-registry settings UI."""

from __future__ import annotations

import json
import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from storage.settings_repository import SettingsRepository
from ui.ai_constants import TYPE_LABELS
from ui.widgets import card_layout, label


PROTOCOLS = {
    "OpenAI 兼容接口": "openai_compatible",
    "OpenAI 官方接口": "openai",
    "Anthropic 接口": "anthropic",
    "Google Gemini 接口": "gemini",
    "自定义 HTTP 接口": "custom",
}


class AISettingsWidget(QWidget):
    def __init__(self, repository: SettingsRepository) -> None:
        super().__init__()
        self.repository = repository
        self.current_provider_id: int | None = None
        self.current_model_id: int | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 16, 0, 0)
        tabs = QTabWidget()
        tabs.setMinimumHeight(640)
        tabs.addTab(self._build_providers_tab(), "API 厂家")
        tabs.addTab(self._build_models_tab(), "模型注册")
        from ui.model_test_widget import ModelTestWidget
        self.test_widget = ModelTestWidget(self.repository)
        tabs.addTab(self.test_widget, "模型测试")
        layout.addWidget(tabs)
        self.refresh_providers()
        self.refresh_models()

    def _build_providers_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 14, 0, 0)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        info = QLabel("厂家配置负责 API 地址、协议和密钥；同一个厂家可以注册多个不同类型的模型。API Key 使用 Windows DPAPI 加密后保存。")
        info.setObjectName("Muted")
        info.setWordWrap(True)
        layout.addWidget(info)

        splitter = QSplitter()
        splitter.setMinimumHeight(440)
        left, left_layout = card_layout()
        header = QHBoxLayout()
        header.addWidget(label("API 厂家", "SectionTitle"))
        header.addStretch()
        add_button = QPushButton("+ 新增厂家")
        add_button.clicked.connect(self.new_provider)
        header.addWidget(add_button)
        left_layout.addLayout(header)
        self.provider_table = self._table(["厂家", "接口协议", "状态"])
        self.provider_table.itemSelectionChanged.connect(self._provider_selected)
        left_layout.addWidget(self.provider_table)
        splitter.addWidget(left)

        right, right_layout = card_layout()
        self.provider_form_title = label("厂家详情", "SectionTitle")
        right_layout.addWidget(self.provider_form_title)
        form = QFormLayout()
        form.setSpacing(12)
        self.provider_name = QLineEdit()
        self.provider_name.setPlaceholderText("例如 OpenAI、DeepSeek、阿里云百炼")
        self.provider_protocol = QComboBox()
        for text, value in PROTOCOLS.items():
            self.provider_protocol.addItem(text, value)
        self.provider_url = QLineEdit()
        self.provider_url.setPlaceholderText("https://api.example.com/v1")
        self.provider_key = QLineEdit()
        self.provider_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.provider_key.setPlaceholderText("留空表示不修改已经保存的 Key")
        self.provider_org = QLineEdit()
        self.provider_org.setPlaceholderText("可选")
        self.provider_timeout = QSpinBox()
        self.provider_timeout.setRange(5, 300)
        self.provider_timeout.setValue(45)
        self.provider_timeout.setSuffix(" 秒")
        self.provider_enabled = QCheckBox("启用该厂家")
        form.addRow("厂家名称", self.provider_name)
        form.addRow("API 协议", self.provider_protocol)
        form.addRow("Base URL", self.provider_url)
        form.addRow("API Key", self.provider_key)
        form.addRow("组织标识", self.provider_org)
        form.addRow("请求超时", self.provider_timeout)
        form.addRow("", self.provider_enabled)
        right_layout.addLayout(form)
        actions = QHBoxLayout()
        self.delete_provider_button = QPushButton("删除厂家")
        self.delete_provider_button.setObjectName("DangerButton")
        self.delete_provider_button.clicked.connect(self.delete_provider)
        actions.addWidget(self.delete_provider_button)
        actions.addStretch()
        test_button = QPushButton("测试连接")
        test_button.clicked.connect(self._test_placeholder)
        actions.addWidget(test_button)
        save_button = QPushButton("保存厂家")
        save_button.setObjectName("Primary")
        save_button.clicked.connect(self.save_provider)
        actions.addWidget(save_button)
        right_layout.addLayout(actions)
        right_layout.addStretch()
        splitter.addWidget(right)
        splitter.setSizes([500, 680])
        layout.addWidget(splitter)
        return page

    def _build_models_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 14, 0, 0)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        toolbar = QHBoxLayout()
        toolbar.addWidget(label("模型注册表", "SectionTitle"))
        toolbar.addStretch()
        self.model_filter = QComboBox()
        self.model_filter.addItem("全部类型", "")
        for value, text in TYPE_LABELS.items():
            self.model_filter.addItem(text, value)
        self.model_filter.currentIndexChanged.connect(self.refresh_models)
        toolbar.addWidget(self.model_filter)
        add_button = QPushButton("+ 新增模型")
        add_button.clicked.connect(self.new_model)
        toolbar.addWidget(add_button)
        layout.addLayout(toolbar)

        self.model_table = self._table(["类型", "显示名称", "API 模型 ID", "厂家", "默认", "状态"])
        self.model_table.setMinimumHeight(235)
        self.model_table.itemSelectionChanged.connect(self._model_selected)
        layout.addWidget(self.model_table)

        form_card, form_layout = card_layout()
        self.model_form_title = label("模型详情", "SectionTitle")
        form_layout.addWidget(self.model_form_title)
        columns = QHBoxLayout()
        left_form = QFormLayout()
        left_form.setSpacing(11)
        self.model_provider = QComboBox()
        self.model_type = QComboBox()
        for value, text in TYPE_LABELS.items():
            self.model_type.addItem(text, value)
        self.model_display_name = QLineEdit()
        self.model_display_name.setPlaceholderText("用于界面展示的名称")
        self.model_api_id = QLineEdit()
        self.model_api_id.setPlaceholderText("厂家要求的 model 参数")
        left_form.addRow("API 厂家", self.model_provider)
        left_form.addRow("模型类型", self.model_type)
        left_form.addRow("显示名称", self.model_display_name)
        left_form.addRow("API 模型 ID", self.model_api_id)
        columns.addLayout(left_form, 1)

        right_form = QFormLayout()
        right_form.setSpacing(11)
        self.model_context = QSpinBox()
        self.model_context.setRange(0, 10_000_000)
        self.model_context.setSpecialValueText("未设置")
        self.model_context.setSuffix(" tokens")
        self.model_dimension = QSpinBox()
        self.model_dimension.setRange(0, 1_000_000)
        self.model_dimension.setSpecialValueText("未设置")
        self.model_temperature = QDoubleSpinBox()
        self.model_temperature.setRange(0, 2)
        self.model_temperature.setSingleStep(0.1)
        self.model_temperature.setValue(0.3)
        self.model_default = QCheckBox("设为该类型的默认模型")
        self.model_enabled = QCheckBox("启用该模型")
        right_form.addRow("上下文长度", self.model_context)
        right_form.addRow("向量维度", self.model_dimension)
        right_form.addRow("Temperature", self.model_temperature)
        right_form.addRow("", self.model_default)
        right_form.addRow("", self.model_enabled)
        columns.addLayout(right_form, 1)
        form_layout.addLayout(columns)
        form_layout.addWidget(label("扩展参数（JSON，将透传给后续模型网关）", "Muted"))
        self.model_extra = QPlainTextEdit("{}")
        self.model_extra.setMaximumHeight(72)
        form_layout.addWidget(self.model_extra)
        actions = QHBoxLayout()
        self.delete_model_button = QPushButton("删除模型")
        self.delete_model_button.setObjectName("DangerButton")
        self.delete_model_button.clicked.connect(self.delete_model)
        actions.addWidget(self.delete_model_button)
        actions.addStretch()
        save_button = QPushButton("保存模型")
        save_button.setObjectName("Primary")
        save_button.clicked.connect(self.save_model)
        actions.addWidget(save_button)
        form_layout.addLayout(actions)
        layout.addWidget(form_card)
        return page

    @staticmethod
    def _table(headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setShowGrid(False)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        return table

    def refresh_providers(self, select_id: int | None = None) -> None:
        providers = self.repository.list_providers()
        self.provider_table.blockSignals(True)
        self.provider_table.setRowCount(len(providers))
        selected_row = 0 if providers else -1
        for row, provider in enumerate(providers):
            values = [provider["name"], self._protocol_label(provider["api_protocol"]), "已启用" if provider["enabled"] else "已停用"]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, provider["id"])
                self.provider_table.setItem(row, column, item)
            if select_id == provider["id"]:
                selected_row = row
        self.provider_table.blockSignals(False)
        self._refresh_provider_combo(providers)
        if selected_row >= 0:
            self.provider_table.selectRow(selected_row)
            self._provider_selected()
        else:
            self.new_provider()

    def _refresh_provider_combo(self, providers: list[dict]) -> None:
        selected = self.model_provider.currentData() if self.model_provider.count() else None
        self.model_provider.clear()
        for provider in providers:
            self.model_provider.addItem(provider["name"], provider["id"])
        index = self.model_provider.findData(selected)
        if index >= 0:
            self.model_provider.setCurrentIndex(index)

    @staticmethod
    def _protocol_label(value: str) -> str:
        return next((name for name, code in PROTOCOLS.items() if code == value), value)

    def _provider_selected(self) -> None:
        items = self.provider_table.selectedItems()
        if not items:
            return
        provider_id = self.provider_table.item(items[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        provider = self.repository.get_provider(provider_id, reveal_key=False)
        if not provider:
            return
        self.current_provider_id = provider_id
        self.provider_form_title.setText(f"厂家详情 · {provider['name']}")
        self.provider_name.setText(provider["name"])
        index = self.provider_protocol.findData(provider["api_protocol"])
        self.provider_protocol.setCurrentIndex(max(index, 0))
        self.provider_url.setText(provider["base_url"])
        self.provider_key.clear()
        self.provider_key.setPlaceholderText("已保存加密 Key；留空表示不修改" if provider["has_api_key"] else "输入 API Key")
        self.provider_org.setText(provider["organization"])
        self.provider_timeout.setValue(provider["timeout_seconds"])
        self.provider_enabled.setChecked(provider["enabled"])
        self.delete_provider_button.setEnabled(True)

    def new_provider(self) -> None:
        self.current_provider_id = None
        self.provider_table.clearSelection()
        self.provider_form_title.setText("新增 API 厂家")
        self.provider_name.clear()
        self.provider_protocol.setCurrentIndex(0)
        self.provider_url.clear()
        self.provider_key.clear()
        self.provider_key.setPlaceholderText("输入 API Key")
        self.provider_org.clear()
        self.provider_timeout.setValue(45)
        self.provider_enabled.setChecked(True)
        self.delete_provider_button.setEnabled(False)
        self.provider_name.setFocus()

    def save_provider(self) -> None:
        values = {
            "name": self.provider_name.text(),
            "api_protocol": self.provider_protocol.currentData(),
            "base_url": self.provider_url.text(),
            "api_key": self.provider_key.text(),
            "organization": self.provider_org.text(),
            "timeout_seconds": self.provider_timeout.value(),
            "enabled": self.provider_enabled.isChecked(),
        }
        try:
            provider_id = self.repository.save_provider(values, self.current_provider_id)
        except (ValueError, sqlite3.IntegrityError, OSError) as error:
            QMessageBox.warning(self, "保存失败", str(error))
            return
        self.refresh_providers(provider_id)
        self.refresh_models()
        QMessageBox.information(self, "保存成功", "API 厂家配置已保存到数据库。")

    def delete_provider(self) -> None:
        if self.current_provider_id is None:
            return
        answer = QMessageBox.question(self, "删除厂家", "删除厂家会同时删除其全部模型配置，确定继续吗？")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.repository.delete_provider(self.current_provider_id)
        self.current_provider_id = None
        self.refresh_providers()
        self.refresh_models()

    def refresh_models(self, select_id: int | None = None) -> None:
        models = self.repository.list_models()
        selected_type = self.model_filter.currentData() if hasattr(self, "model_filter") else ""
        if selected_type:
            models = [model for model in models if model["model_type"] == selected_type]
        self.model_table.blockSignals(True)
        self.model_table.setRowCount(len(models))
        selected_row = 0 if models else -1
        for row, model in enumerate(models):
            values = [TYPE_LABELS[model["model_type"]], model["display_name"], model["model_id"], model["provider_name"], "是" if model["is_default"] else "—", "已启用" if model["enabled"] else "已停用"]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, model["id"])
                self.model_table.setItem(row, column, item)
            if select_id == model["id"]:
                selected_row = row
        self.model_table.blockSignals(False)
        if selected_row >= 0:
            self.model_table.selectRow(selected_row)
            self._model_selected()
        else:
            self.new_model()
        if hasattr(self, "test_widget"):
            self.test_widget.refresh_models()

    def _model_selected(self) -> None:
        items = self.model_table.selectedItems()
        if not items:
            return
        model_id = self.model_table.item(items[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        model = self.repository.get_model(model_id)
        if not model:
            return
        self.current_model_id = model_id
        self.model_form_title.setText(f"模型详情 · {model['display_name']}")
        self.model_provider.setCurrentIndex(max(self.model_provider.findData(model["provider_id"]), 0))
        self.model_type.setCurrentIndex(max(self.model_type.findData(model["model_type"]), 0))
        self.model_display_name.setText(model["display_name"])
        self.model_api_id.setText(model["model_id"])
        self.model_context.setValue(model["context_length"] or 0)
        self.model_dimension.setValue(model["vector_dimension"] or 0)
        self.model_temperature.setValue(model["temperature"])
        self.model_default.setChecked(model["is_default"])
        self.model_enabled.setChecked(model["enabled"])
        self.model_extra.setPlainText(json.dumps(model["extra_json"], ensure_ascii=False, indent=2))
        self.delete_model_button.setEnabled(True)

    def new_model(self) -> None:
        self.current_model_id = None
        self.model_table.clearSelection()
        self.model_form_title.setText("新增模型")
        self.model_type.setCurrentIndex(0)
        self.model_display_name.clear()
        self.model_api_id.clear()
        self.model_context.setValue(0)
        self.model_dimension.setValue(0)
        self.model_temperature.setValue(0.3)
        self.model_default.setChecked(False)
        self.model_enabled.setChecked(True)
        self.model_extra.setPlainText("{}")
        self.delete_model_button.setEnabled(False)

    def save_model(self) -> None:
        if self.model_provider.currentData() is None:
            QMessageBox.warning(self, "保存失败", "请先创建一个 API 厂家。")
            return
        try:
            extra = json.loads(self.model_extra.toPlainText().strip() or "{}")
            if not isinstance(extra, dict):
                raise ValueError("扩展参数必须是 JSON 对象")
            values = {
                "provider_id": self.model_provider.currentData(),
                "model_type": self.model_type.currentData(),
                "display_name": self.model_display_name.text(),
                "model_id": self.model_api_id.text(),
                "context_length": self.model_context.value(),
                "vector_dimension": self.model_dimension.value(),
                "temperature": self.model_temperature.value(),
                "is_default": self.model_default.isChecked(),
                "enabled": self.model_enabled.isChecked(),
                "extra_json": extra,
            }
            model_id = self.repository.save_model(values, self.current_model_id)
        except (ValueError, sqlite3.IntegrityError) as error:
            QMessageBox.warning(self, "保存失败", str(error))
            return
        self.refresh_models(model_id)
        QMessageBox.information(self, "保存成功", "模型配置已保存到数据库。")

    def delete_model(self) -> None:
        if self.current_model_id is None:
            return
        answer = QMessageBox.question(self, "删除模型", "确定删除当前模型配置吗？")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.repository.delete_model(self.current_model_id)
        self.current_model_id = None
        self.refresh_models()

    def _test_placeholder(self) -> None:
        QMessageBox.information(self, "测试连接", "厂家配置已就绪；实际 API 连通性测试将在模型网关阶段接入。")
