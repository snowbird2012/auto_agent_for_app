"""Model strategy configuration page."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView,QComboBox,QFormLayout,QHBoxLayout,
    QHeaderView,QLineEdit,QListWidget,QListWidgetItem,QMessageBox,QPlainTextEdit,
    QPushButton,QTableWidget,QTableWidgetItem,QVBoxLayout,QWidget)
from storage import KnowledgeRepository, MessageStrategyRepository, SettingsRepository
from ui.widgets import SectionHeader, card_layout, label


class MessageStrategyWidget(QWidget):
    def __init__(self, repository: MessageStrategyRepository, settings: SettingsRepository,
                 knowledge_repository: KnowledgeRepository) -> None:
        super().__init__(); self.repository=repository; self.settings=settings
        self.knowledge_repository=knowledge_repository; self.current_id=None
        layout=QVBoxLayout(self); layout.setContentsMargins(26,24,26,28); layout.setSpacing(18)
        layout.addWidget(SectionHeader("消息策略","配置模型判断是否回复，并生成结构化回复内容"))
        card,body=card_layout(); self.table=QTableWidget(0,4)
        self.table.setObjectName("MessageStrategyTable")
        self.table.setHorizontalHeaderLabels(["策略名字","模型","绑定知识库","系统级提示词"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(3,QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._selected); body.addWidget(self.table)
        layout.addWidget(card)
        form_card,form_body=card_layout(); form_body.addWidget(label("新增/编辑策略","SectionTitle")); form=QFormLayout()
        self.name=QLineEdit(); self.model=QComboBox(); self.knowledge_bases=QListWidget(); self.prompt=QPlainTextEdit()
        self.knowledge_bases.setMinimumHeight(120); self.knowledge_bases.setMaximumHeight(210)
        self.prompt.setPlaceholderText("例如：判断用户是否需要业务回复；需要时生成简洁、友善的答复。")
        self.prompt.setMinimumHeight(130); form.addRow("策略名字",self.name); form.addRow("选择模型",self.model)
        form.addRow("绑定知识库（可多选）",self.knowledge_bases); form.addRow("系统级提示词",self.prompt); form_body.addLayout(form)
        actions=QHBoxLayout(); save=QPushButton("保存策略"); save.setObjectName("Primary"); save.clicked.connect(self.save)
        clear=QPushButton("新建"); clear.clicked.connect(self.clear); delete=QPushButton("删除"); delete.clicked.connect(self.delete)
        actions.addWidget(save); actions.addWidget(clear); actions.addWidget(delete); actions.addStretch(); form_body.addLayout(actions); layout.addWidget(form_card); layout.addStretch()
        self.refresh_all()

    def refresh_all(self):
        current=self.model.currentData(); self.model.clear()
        for item in self.settings.list_models("llm",enabled_only=True):
            self.model.addItem(f'{item["provider_name"]} · {item["display_name"]}',item["id"])
        idx=self.model.findData(current)
        if idx>=0:self.model.setCurrentIndex(idx)
        checked=set(self.checked_knowledge_base_ids()); self.knowledge_bases.clear(); base_names={}
        for base in self.knowledge_repository.list_bases():
            base_id=int(base["id"]); enabled=bool(base["enabled"]); base_names[base_id]=base["name"]
            suffix=f' · {base["document_count"]} 个文档' if enabled else " · 已停用"
            item=QListWidgetItem(f'{base["name"]}{suffix}'); item.setData(Qt.ItemDataRole.UserRole,base_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            if not enabled:item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            item.setCheckState(Qt.CheckState.Checked if base_id in checked else Qt.CheckState.Unchecked)
            self.knowledge_bases.addItem(item)
        rows=self.repository.list(); self.table.setRowCount(len(rows))
        for r,item in enumerate(rows):
            names=[base_names.get(base_id,f'已删除 #{base_id}') for base_id in item["knowledge_base_ids"]]
            for c,value in enumerate((item["name"],f'{item["provider_name"]} · {item["model_name"]}',"、".join(names) or "未绑定",item["system_prompt"])):
                cell=QTableWidgetItem(str(value)); self.table.setItem(r,c,cell)
                if c==0: cell.setData(Qt.ItemDataRole.UserRole,item["id"])

    def _selected(self):
        row=self.table.currentRow(); item=self.table.item(row,0) if row>=0 else None
        record=self.repository.get(int(item.data(Qt.ItemDataRole.UserRole))) if item else None
        if not record:return
        self.current_id=record["id"]; self.name.setText(record["name"]); self.prompt.setPlainText(record["system_prompt"])
        idx=self.model.findData(record["model_id"])
        if idx>=0:self.model.setCurrentIndex(idx)
        self.set_checked_knowledge_bases(record.get("knowledge_base_ids",[]))

    def checked_knowledge_base_ids(self):
        return [int(self.knowledge_bases.item(index).data(Qt.ItemDataRole.UserRole))
                for index in range(self.knowledge_bases.count())
                if self.knowledge_bases.item(index).checkState()==Qt.CheckState.Checked]

    def set_checked_knowledge_bases(self,base_ids):
        selected={int(value) for value in base_ids}
        for index in range(self.knowledge_bases.count()):
            item=self.knowledge_bases.item(index)
            item.setCheckState(Qt.CheckState.Checked if int(item.data(Qt.ItemDataRole.UserRole)) in selected else Qt.CheckState.Unchecked)

    def clear(self):
        self.current_id=None; self.name.clear(); self.prompt.clear(); self.set_checked_knowledge_bases([])
    def save(self):
        try:self.current_id=self.repository.save({"name":self.name.text(),"model_id":self.model.currentData(),"knowledge_base_ids":self.checked_knowledge_base_ids(),"system_prompt":self.prompt.toPlainText()},self.current_id)
        except Exception as error: QMessageBox.warning(self,"保存失败",str(error)); return
        self.refresh_all()
    def delete(self):
        if self.current_id is None:return
        try:self.repository.delete(self.current_id)
        except Exception as error: QMessageBox.warning(self,"删除失败",str(error)); return
        self.clear(); self.refresh_all()
