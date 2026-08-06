"""Local SQLite/NumPy persistence for knowledge bases and embeddings."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import hashlib
import sqlite3
from typing import Iterator

import numpy as np


class KnowledgeRepository:
    def __init__(self, database_path: str | Path | None = None) -> None:
        root = Path(__file__).resolve().parents[1]
        self.database_path = Path(database_path) if database_path else root / "data" / "knowledge.db"
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.database_path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback(); raise
        finally:
            db.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS knowledge_bases(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                embedding_model_id INTEGER NOT NULL,
                chunk_size INTEGER NOT NULL DEFAULT 700,
                chunk_overlap INTEGER NOT NULL DEFAULT 100,
                top_k INTEGER NOT NULL DEFAULT 5,
                min_score REAL NOT NULL DEFAULT 0.35,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS knowledge_documents(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                knowledge_base_id INTEGER NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
                file_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                file_hash TEXT NOT NULL,
                encoding TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'indexed',
                error_message TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(knowledge_base_id,file_path)
            );
            CREATE TABLE IF NOT EXISTS knowledge_chunks(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                embedding BLOB NOT NULL,
                embedding_dimension INTEGER NOT NULL,
                embedding_model_id INTEGER NOT NULL,
                content_hash TEXT NOT NULL,
                UNIQUE(document_id,chunk_index)
            );
            CREATE INDEX IF NOT EXISTS ix_chunks_document ON knowledge_chunks(document_id);
            """)

    def list_bases(self) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("""SELECT b.*,COUNT(DISTINCT d.id) document_count,
                COUNT(c.id) chunk_count FROM knowledge_bases b
                LEFT JOIN knowledge_documents d ON d.knowledge_base_id=b.id
                LEFT JOIN knowledge_chunks c ON c.document_id=d.id
                GROUP BY b.id ORDER BY b.updated_at DESC,b.id DESC""").fetchall()
        return [dict(row) for row in rows]

    def get_base(self, base_id: int) -> dict | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM knowledge_bases WHERE id=?", (base_id,)).fetchone()
        return dict(row) if row else None

    def save_base(self, values: dict, base_id: int | None = None) -> int:
        name = str(values.get("name", "")).strip()
        model_id = values.get("embedding_model_id")
        if not name: raise ValueError("知识库名称不能为空")
        if model_id is None: raise ValueError("必须选择向量模型")
        size = max(100, min(4000, int(values.get("chunk_size", 700))))
        overlap = max(0, min(size // 2, int(values.get("chunk_overlap", 100))))
        payload = (name, str(values.get("description", "")).strip(), int(model_id), size,
                   overlap, max(1, min(30, int(values.get("top_k", 5)))),
                   max(0.0, min(1.0, float(values.get("min_score", .35)))),
                   int(bool(values.get("enabled", True))))
        with self._connect() as db:
            if base_id is None:
                cursor = db.execute("""INSERT INTO knowledge_bases
                    (name,description,embedding_model_id,chunk_size,chunk_overlap,top_k,min_score,enabled)
                    VALUES(?,?,?,?,?,?,?,?)""", payload)
                return int(cursor.lastrowid)
            db.execute("""UPDATE knowledge_bases SET name=?,description=?,embedding_model_id=?,
                chunk_size=?,chunk_overlap=?,top_k=?,min_score=?,enabled=?,updated_at=CURRENT_TIMESTAMP
                WHERE id=?""", payload + (base_id,))
            return base_id

    def delete_base(self, base_id: int) -> None:
        with self._connect() as db: db.execute("DELETE FROM knowledge_bases WHERE id=?", (base_id,))

    def list_documents(self, base_id: int) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("""SELECT d.*,COUNT(c.id) chunk_count FROM knowledge_documents d
                LEFT JOIN knowledge_chunks c ON c.document_id=d.id
                WHERE d.knowledge_base_id=? GROUP BY d.id ORDER BY d.updated_at DESC""", (base_id,)).fetchall()
        return [dict(row) for row in rows]

    def delete_document(self, document_id: int) -> None:
        with self._connect() as db: db.execute("DELETE FROM knowledge_documents WHERE id=?", (document_id,))

    def replace_document(self, base_id: int, path: Path, encoding: str,
                         chunks: list[str], vectors: list[list[float]], model_id: int) -> int:
        if len(chunks) != len(vectors): raise ValueError("文本片段与向量数量不一致")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        with self._connect() as db:
            existing = db.execute("SELECT id FROM knowledge_documents WHERE knowledge_base_id=? AND file_path=?",
                                  (base_id, str(path.resolve()))).fetchone()
            if existing:
                document_id = int(existing["id"]); db.execute("DELETE FROM knowledge_chunks WHERE document_id=?", (document_id,))
                db.execute("""UPDATE knowledge_documents SET file_name=?,file_hash=?,encoding=?,status='indexed',
                    error_message='',updated_at=CURRENT_TIMESTAMP WHERE id=?""", (path.name,digest,encoding,document_id))
            else:
                cursor = db.execute("""INSERT INTO knowledge_documents
                    (knowledge_base_id,file_name,file_path,file_hash,encoding,status)
                    VALUES(?,?,?,?,?,'indexed')""", (base_id,path.name,str(path.resolve()),digest,encoding))
                document_id = int(cursor.lastrowid)
            for index, (content, vector) in enumerate(zip(chunks, vectors)):
                array = np.asarray(vector, dtype=np.float32)
                norm = float(np.linalg.norm(array))
                if norm: array = array / norm
                db.execute("""INSERT INTO knowledge_chunks
                    (document_id,chunk_index,content,embedding,embedding_dimension,embedding_model_id,content_hash)
                    VALUES(?,?,?,?,?,?,?)""", (document_id,index,content,array.tobytes(),array.size,model_id,
                    hashlib.sha256(content.encode('utf-8')).hexdigest()))
        return document_id

    def search(self, base_id: int, query_vector: list[float], top_k: int, min_score: float) -> list[dict]:
        query = np.asarray(query_vector, dtype=np.float32)
        norm = float(np.linalg.norm(query))
        if not norm: return []
        query /= norm
        with self._connect() as db:
            rows = db.execute("""SELECT c.id,c.content,c.embedding,c.embedding_dimension,
                c.chunk_index,d.file_name FROM knowledge_chunks c JOIN knowledge_documents d ON d.id=c.document_id
                WHERE d.knowledge_base_id=? AND d.status='indexed'""", (base_id,)).fetchall()
        scored = []
        for row in rows:
            vector = np.frombuffer(row["embedding"], dtype=np.float32)
            if vector.size != query.size: continue
            score = float(vector @ query)
            if score >= min_score:
                item = dict(row); item.pop("embedding", None); item["score"] = score; scored.append(item)
        return sorted(scored, key=lambda item: item["score"], reverse=True)[:top_k]
