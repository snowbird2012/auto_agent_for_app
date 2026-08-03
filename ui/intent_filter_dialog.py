"""Configuration dialog for bulk LLM intent screening."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
)

from storage import SettingsRepository


DEFAULT_INTENT_PROMPT = (
    "如果用户标签中有“股票”，则根据该用户的全部留言判断其是否具有购买相关产品或服务的意图。"
    "只有表达明确需求、询价、咨询购买方式或主动寻求解决方案时才判断为有意向；"
    "普通讨论、点赞、感谢或无关内容判断为无意向。"
)


class IntentFilterDialog(QDialog):
    def __init__(self, repository: SettingsRepository, parent=None) -> None:
        super().__init__(parent)
        self.repository = repository
        self.models: dict[int, dict] = {}
        self.setWindowTitle("意向判断设置")
        self.resize(650, 430)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 18)
        layout.setSpacing(14)

        note = QLabel(
            "将所有来源为“视频”或“直播”的用户及其全部留言一次性提交给所选大语言模型。"
            "模型只生成预览，确认后才会修改或删除数据。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        form = QFormLayout()
        self.model_combo = QComboBox()
        saved = repository.get_setting("intent_filter", {})
        for model in repository.list_models():
            if model["model_type"] != "llm" or not model["enabled"]:
                continue
            self.models[int(model["id"])] = model
            self.model_combo.addItem(
                f'{model["display_name"]} · {model["provider_name"]} · {model["model_id"]}',
                int(model["id"]),
            )
        saved_model = saved.get("model_id") if isinstance(saved, dict) else None
        index = self.model_combo.findData(saved_model)
        if index >= 0:
            self.model_combo.setCurrentIndex(index)
        form.addRow("大语言模型", self.model_combo)
        layout.addLayout(form)

        layout.addWidget(QLabel("意向判断提示词"))
        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setPlainText(
            saved.get("prompt", DEFAULT_INTENT_PROMPT)
            if isinstance(saved, dict) else DEFAULT_INTENT_PROMPT
        )
        self.prompt_edit.setMinimumHeight(210)
        layout.addWidget(self.prompt_edit)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("开始判断")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.accepted.connect(self._accept_if_valid)
        self.buttons.rejected.connect(self.reject)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(self.models))
        layout.addWidget(self.buttons)

    def _accept_if_valid(self) -> None:
        if self.model_combo.currentData() is None or not self.prompt_edit.toPlainText().strip():
            return
        self.repository.set_setting("intent_filter", {
            "model_id": int(self.model_combo.currentData()),
            "prompt": self.prompt_edit.toPlainText().strip(),
        })
        self.accept()

    def values(self) -> tuple[int, str]:
        return int(self.model_combo.currentData()), self.prompt_edit.toPlainText().strip()
