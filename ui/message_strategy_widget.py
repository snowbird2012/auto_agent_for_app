"""Model strategy configuration page."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView,QComboBox,QFormLayout,QHBoxLayout,
    QHeaderView,QLineEdit,QMessageBox,QPlainTextEdit,QPushButton,QTableWidget,
    QTableWidgetItem,QVBoxLayout,QWidget)
from storage import MessageStrategyRepository, SettingsRepository
from ui.widgets import SectionHeader, card_layout, label


class MessageStrategyWidget(QWidget):
    def __init__(self, repository: MessageStrategyRepository, settings: SettingsRepository) -> None:
        super().__init__(); self.repository=repository; self.settings=settings; self.current_id=None
        layout=QVBoxLayout(self); layout.setContentsMargins(26,24,26,28); layout.setSpacing(18)
        layout.addWidget(SectionHeader("消息策略","配置模型判断是否回复，并生成结构化回复内容"))
        card,body=card_layout(); self.table=QTableWidget(0,3)
        self.table.setObjectName("MessageStrategyTable")
        self.table.setHorizontalHeaderLabels(["策略名字","模型","系统级提示词"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._selected); body.addWidget(self.table)
        layout.addWidget(card)
        form_card,form_body=card_layout(); form_body.addWidget(label("新增/编辑策略","SectionTitle")); form=QFormLayout()
        self.name=QLineEdit(); self.model=QComboBox(); self.prompt=QPlainTextEdit()
        self.prompt.setPlaceholderText("例如：判断用户是否需要业务回复；需要时生成简洁、友善的答复。")
        self.prompt.setMinimumHeight(130); form.addRow("策略名字",self.name); form.addRow("选择模型",self.model); form.addRow("系统级提示词",self.prompt); form_body.addLayout(form)
        actions=QHBoxLayout(); save=QPushButton("保存策略"); save.setObjectName("Primary"); save.clicked.connect(self.save)
        clear=QPushButton("新建"); clear.clicked.connect(self.clear); delete=QPushButton("删除"); delete.clicked.connect(self.delete)
        actions.addWidget(save); actions.addWidget(clear); actions.addWidget(delete); actions.addStretch(); form_body.addLayout(actions); layout.addWidget(form_card); layout.addStretch()
        self.refresh_all()

    def refresh_all(self):
        current=self.model.currentData(); self.model.clear()
        for item in self.settings.list_models():
            if item["model_type"]=="llm" and item["enabled"]: self.model.addItem(f'{item["provider_name"]} · {item["display_name"]}',item["id"])
        idx=self.model.findData(current)
        if idx>=0:self.model.setCurrentIndex(idx)
        rows=self.repository.list(); self.table.setRowCount(len(rows))
        for r,item in enumerate(rows):
            for c,value in enumerate((item["name"],f'{item["provider_name"]} · {item["model_name"]}',item["system_prompt"])):
                cell=QTableWidgetItem(str(value)); self.table.setItem(r,c,cell)
                if c==0: cell.setData(Qt.ItemDataRole.UserRole,item["id"])

    def _selected(self):
        row=self.table.currentRow(); item=self.table.item(row,0) if row>=0 else None
        record=self.repository.get(int(item.data(Qt.ItemDataRole.UserRole))) if item else None
        if not record:return
        self.current_id=record["id"]; self.name.setText(record["name"]); self.prompt.setPlainText(record["system_prompt"])
        idx=self.model.findData(record["model_id"])
        if idx>=0:self.model.setCurrentIndex(idx)

    def clear(self): self.current_id=None; self.name.clear(); self.prompt.clear()
    def save(self):
        try:self.current_id=self.repository.save({"name":self.name.text(),"model_id":self.model.currentData(),"system_prompt":self.prompt.toPlainText()},self.current_id)
        except Exception as error: QMessageBox.warning(self,"保存失败",str(error)); return
        self.refresh_all()
    def delete(self):
        if self.current_id is None:return
        try:self.repository.delete(self.current_id)
        except Exception as error: QMessageBox.warning(self,"删除失败",str(error)); return
        self.clear(); self.refresh_all()
