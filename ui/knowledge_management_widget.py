"""Knowledge-base CRUD, TXT indexing and retrieval test UI."""

from __future__ import annotations

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QHBoxLayout, QHeaderView, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QSpinBox, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from services.knowledge_service import KnowledgeService
from storage import KnowledgeRepository, SettingsRepository
from ui.widgets import SectionHeader, card_layout, label


class KnowledgeIndexWorker(QThread):
    progress = Signal(str)
    succeeded = Signal(int)
    failed = Signal(str)

    def __init__(self, service: KnowledgeService, base_id: int, paths: list[str]) -> None:
        super().__init__(); self.service=service; self.base_id=base_id; self.paths=paths

    def run(self) -> None:
        try:
            total = 0
            for path in self.paths:
                self.progress.emit(f"正在建立索引：{path}")
                total += self.service.index_file(self.base_id, path)
            self.succeeded.emit(total)
        except Exception as error: self.failed.emit(str(error))


class KnowledgeSearchWorker(QThread):
    succeeded = Signal(list)
    failed = Signal(str)

    def __init__(self, service: KnowledgeService, base_id: int, query: str) -> None:
        super().__init__(); self.service=service; self.base_id=base_id; self.query=query

    def run(self) -> None:
        try: self.succeeded.emit(self.service.search(self.base_id, self.query))
        except Exception as error: self.failed.emit(str(error))


class KnowledgeManagementWidget(QWidget):
    def __init__(self, repository: KnowledgeRepository, settings: SettingsRepository) -> None:
        super().__init__(); self.repository=repository; self.settings=settings
        self.service=KnowledgeService(repository, settings); self.current_id=None
        self.index_worker=None; self.search_worker=None
        self._build_ui(); self.refresh_all()

    def _build_ui(self) -> None:
        layout=QVBoxLayout(self); layout.setContentsMargins(26,24,26,28); layout.setSpacing(16)
        layout.addWidget(SectionHeader("知识库管理","导入 TXT 文件，建立本地向量索引并测试知识检索"))
        splitter=QSplitter(Qt.Orientation.Horizontal)

        left,left_body=card_layout(); self.base_table=QTableWidget(0,5)
        self.base_table.setHorizontalHeaderLabels(["知识库","向量模型","文档","片段","状态"])
        self.base_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.base_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.base_table.setAlternatingRowColors(True); self.base_table.setShowGrid(False)
        self.base_table.verticalHeader().setVisible(False)
        self.base_table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch)
        self.base_table.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch)
        self.base_table.itemSelectionChanged.connect(self._base_selected)
        left_body.addWidget(self.base_table)
        buttons=QHBoxLayout(); new=QPushButton("新建"); new.clicked.connect(self.clear_form)
        delete=QPushButton("删除知识库"); delete.setObjectName("DangerButton"); delete.clicked.connect(self.delete_base)
        buttons.addWidget(new); buttons.addWidget(delete); buttons.addStretch(); left_body.addLayout(buttons)
        splitter.addWidget(left)

        right,right_body=card_layout(); right_body.addWidget(label("知识库设置","SectionTitle"))
        form=QFormLayout(); self.name=QLineEdit(); self.description=QLineEdit(); self.model=QComboBox()
        self.chunk_size=QSpinBox(); self.chunk_size.setRange(100,4000); self.chunk_size.setValue(700)
        self.overlap=QSpinBox(); self.overlap.setRange(0,1000); self.overlap.setValue(100)
        self.top_k=QSpinBox(); self.top_k.setRange(1,30); self.top_k.setValue(5)
        self.min_score=QDoubleSpinBox(); self.min_score.setRange(0,1); self.min_score.setDecimals(2); self.min_score.setSingleStep(.05); self.min_score.setValue(.35)
        self.enabled=QCheckBox("启用知识库"); self.enabled.setChecked(True)
        form.addRow("名称",self.name); form.addRow("说明",self.description); form.addRow("向量模型",self.model)
        form.addRow("分段大小",self.chunk_size); form.addRow("重叠字符",self.overlap)
        form.addRow("召回数量",self.top_k); form.addRow("最低相关度",self.min_score); form.addRow("",self.enabled)
        right_body.addLayout(form); save=QPushButton("保存知识库"); save.setObjectName("Primary"); save.clicked.connect(self.save_base); right_body.addWidget(save)
        splitter.addWidget(right); splitter.setSizes([760,440]); layout.addWidget(splitter,2)

        docs,docs_body=card_layout(); title_row=QHBoxLayout(); title_row.addWidget(label("知识文档","SectionTitle")); title_row.addStretch()
        add=QPushButton("导入 TXT"); add.setObjectName("Primary"); add.clicked.connect(self.add_files)
        rebuild=QPushButton("重建索引"); rebuild.clicked.connect(self.rebuild_document)
        remove=QPushButton("删除文档"); remove.clicked.connect(self.delete_document)
        title_row.addWidget(add); title_row.addWidget(rebuild); title_row.addWidget(remove); docs_body.addLayout(title_row)
        self.doc_table=QTableWidget(0,4); self.doc_table.setHorizontalHeaderLabels(["文件名","编码","片段数","状态"])
        self.doc_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); self.doc_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.doc_table.setShowGrid(False); self.doc_table.verticalHeader().setVisible(False); self.doc_table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch)
        self.doc_table.setMinimumHeight(260); self.doc_table.setMaximumHeight(520)
        docs_body.addWidget(self.doc_table); self.status=label("等待操作","Muted"); docs_body.addWidget(self.status); layout.addWidget(docs,2)

        search,search_body=card_layout(); search.setMinimumHeight(500); search.setMaximumHeight(900)
        search_body.addWidget(label("检索测试","SectionTitle"))
        row=QHBoxLayout(); self.query=QLineEdit(); self.query.setPlaceholderText("输入问题测试知识检索")
        search_button=QPushButton("检索测试"); search_button.clicked.connect(self.search); row.addWidget(self.query,1); row.addWidget(search_button); search_body.addLayout(row)
        self.results=QPlainTextEdit(); self.results.setReadOnly(True)
        self.results.setMinimumHeight(380); self.results.setMaximumHeight(760)
        search_body.addWidget(self.results,1); layout.addWidget(search)

    def refresh_all(self) -> None:
        selected_model=self.model.currentData(); self.model.clear(); models={}
        for item in self.settings.list_models("embedding", enabled_only=True):
            self.model.addItem(f'{item["provider_name"]} · {item["display_name"]}',item["id"]); models[item["id"]]=f'{item["provider_name"]} · {item["display_name"]}'
        index=self.model.findData(selected_model)
        if index>=0:self.model.setCurrentIndex(index)
        rows=self.repository.list_bases(); self.base_table.setRowCount(len(rows))
        for r,item in enumerate(rows):
            values=(item["name"],models.get(item["embedding_model_id"],"模型不可用"),item["document_count"],item["chunk_count"],"启用" if item["enabled"] else "停用")
            for c,value in enumerate(values):
                cell=QTableWidgetItem(str(value)); self.base_table.setItem(r,c,cell)
                if c==0:cell.setData(Qt.ItemDataRole.UserRole,item["id"])
        self.refresh_documents()

    def _base_selected(self) -> None:
        row=self.base_table.currentRow(); cell=self.base_table.item(row,0) if row>=0 else None
        record=self.repository.get_base(int(cell.data(Qt.ItemDataRole.UserRole))) if cell else None
        if not record:return
        self.current_id=record["id"]; self.name.setText(record["name"]); self.description.setText(record["description"])
        self.chunk_size.setValue(record["chunk_size"]); self.overlap.setValue(record["chunk_overlap"]); self.top_k.setValue(record["top_k"]); self.min_score.setValue(record["min_score"]); self.enabled.setChecked(bool(record["enabled"]))
        index=self.model.findData(record["embedding_model_id"])
        if index>=0:self.model.setCurrentIndex(index)
        self.refresh_documents()

    def clear_form(self) -> None:
        self.current_id=None; self.name.clear(); self.description.clear(); self.doc_table.setRowCount(0); self.results.clear()

    def save_base(self) -> None:
        try:
            self.current_id=self.repository.save_base({"name":self.name.text(),"description":self.description.text(),"embedding_model_id":self.model.currentData(),"chunk_size":self.chunk_size.value(),"chunk_overlap":self.overlap.value(),"top_k":self.top_k.value(),"min_score":self.min_score.value(),"enabled":self.enabled.isChecked()},self.current_id)
        except Exception as error: QMessageBox.warning(self,"保存失败",str(error)); return
        self.refresh_all(); self.status.setText("知识库设置已保存")

    def delete_base(self) -> None:
        if self.current_id is None:return
        if QMessageBox.question(self,"删除知识库","将同时删除所有文档和向量，是否继续？") != QMessageBox.StandardButton.Yes:return
        self.repository.delete_base(self.current_id); self.clear_form(); self.refresh_all()

    def refresh_documents(self) -> None:
        rows=self.repository.list_documents(self.current_id) if self.current_id else []; self.doc_table.setRowCount(len(rows))
        for r,item in enumerate(rows):
            for c,value in enumerate((item["file_name"],item["encoding"],item["chunk_count"],item["status"])):
                cell=QTableWidgetItem(str(value)); self.doc_table.setItem(r,c,cell)
                if c==0:
                    cell.setData(Qt.ItemDataRole.UserRole,item["id"])
                    cell.setData(int(Qt.ItemDataRole.UserRole)+1,item["file_path"])

    def add_files(self) -> None:
        if self.index_worker is not None: QMessageBox.information(self,"导入文档","已有索引任务正在执行。"); return
        if self.current_id is None: QMessageBox.information(self,"导入文档","请先保存知识库。"); return
        paths,_=QFileDialog.getOpenFileNames(self,"选择知识文件","","文本文件 (*.txt)")
        if not paths:return
        self.index_worker=KnowledgeIndexWorker(self.service,self.current_id,paths); self.index_worker.progress.connect(self.status.setText)
        self.index_worker.succeeded.connect(self._index_done); self.index_worker.failed.connect(lambda message: QMessageBox.warning(self,"索引失败",message)); self.index_worker.finished.connect(self._index_finished)
        self.status.setText("正在建立向量索引……"); self.index_worker.start()

    def rebuild_document(self) -> None:
        row=self.doc_table.currentRow(); cell=self.doc_table.item(row,0) if row>=0 else None
        if not cell or self.current_id is None:return
        path=str(cell.data(int(Qt.ItemDataRole.UserRole)+1) or "")
        if not path: QMessageBox.warning(self,"重建失败","没有找到文档原始路径。"); return
        if self.index_worker is not None: QMessageBox.information(self,"重建索引","已有索引任务正在执行。"); return
        self.index_worker=KnowledgeIndexWorker(self.service,self.current_id,[path]); self.index_worker.progress.connect(self.status.setText)
        self.index_worker.succeeded.connect(self._index_done); self.index_worker.failed.connect(lambda message: QMessageBox.warning(self,"索引失败",message)); self.index_worker.finished.connect(self._index_finished); self.index_worker.start()

    def _index_done(self, count: int) -> None:
        self.status.setText(f"索引完成，共生成 {count} 个知识片段"); self.refresh_all()

    def _index_finished(self) -> None:
        worker=self.index_worker; self.index_worker=None
        if worker:worker.deleteLater()

    def delete_document(self) -> None:
        row=self.doc_table.currentRow(); cell=self.doc_table.item(row,0) if row>=0 else None
        if not cell:return
        self.repository.delete_document(int(cell.data(Qt.ItemDataRole.UserRole))); self.refresh_all()

    def search(self) -> None:
        if self.search_worker is not None: return
        query=self.query.text().strip()
        if self.current_id is None or not query: QMessageBox.information(self,"检索测试","请选择知识库并输入问题。"); return
        self.results.setPlainText("正在生成查询向量并检索……")
        self.search_worker=KnowledgeSearchWorker(self.service,self.current_id,query); self.search_worker.succeeded.connect(self._search_done); self.search_worker.failed.connect(lambda message:self.results.setPlainText("检索失败："+message)); self.search_worker.finished.connect(self._search_finished); self.search_worker.start()

    def _search_done(self, rows: list) -> None:
        if not rows:self.results.setPlainText("没有达到最低相关度的知识片段。"); return
        self.results.setPlainText("\n\n".join(f'[{index}] 相似度 {item["score"]:.4f} · {item["file_name"]} · 片段 {item["chunk_index"]+1}\n{item["content"]}' for index,item in enumerate(rows,1)))

    def _search_finished(self) -> None:
        worker=self.search_worker; self.search_worker=None
        if worker:worker.deleteLater()

    def shutdown(self) -> None:
        for worker in (self.index_worker,self.search_worker):
            if worker and worker.isRunning():worker.wait(3000)
