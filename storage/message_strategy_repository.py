"""Persistence for model-backed inbox reply strategies."""
from __future__ import annotations
from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Any, Iterator


class MessageStrategyRepository:
    def __init__(self, database_path: str | Path | None = None) -> None:
        root = Path(__file__).resolve().parents[1]
        self.database_path = Path(database_path) if database_path else root / "data" / "autoagent.db"
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.database_path); db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db; db.commit()
        except Exception:
            db.rollback(); raise
        finally:
            db.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript("""CREATE TABLE IF NOT EXISTS message_strategies(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                model_id INTEGER NOT NULL REFERENCES ai_models(id) ON DELETE RESTRICT,
                system_prompt TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS message_strategy_knowledge_bases(
                strategy_id INTEGER NOT NULL REFERENCES message_strategies(id) ON DELETE CASCADE,
                knowledge_base_id INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(strategy_id,knowledge_base_id)
            );
            CREATE INDEX IF NOT EXISTS ix_strategy_knowledge_base
                ON message_strategy_knowledge_bases(knowledge_base_id);
            """)

    @staticmethod
    def _knowledge_base_ids(values: dict[str, Any]) -> list[int]:
        raw = values.get("knowledge_base_ids") or []
        if isinstance(raw, (str, bytes)):
            raise ValueError("知识库绑定参数格式不正确")
        result: set[int] = set()
        try:
            for value in raw:
                base_id=int(value)
                if base_id>0:result.add(base_id)
        except (TypeError,ValueError) as error:
            raise ValueError("知识库绑定参数格式不正确") from error
        return sorted(result)

    def save(self, values: dict[str, Any], strategy_id: int | None = None) -> int:
        name = str(values.get("name", "")).strip()
        prompt = str(values.get("system_prompt", "")).strip()
        model_id = int(values.get("model_id") or 0)
        knowledge_base_ids = self._knowledge_base_ids(values)
        if not name: raise ValueError("策略名字不能为空")
        if model_id <= 0: raise ValueError("请选择大语言模型")
        if not prompt: raise ValueError("系统级提示词不能为空")
        with self._connect() as db:
            model = db.execute("SELECT model_type,enabled FROM ai_models WHERE id=?", (model_id,)).fetchone()
            if not model or model["model_type"] != "llm" or not model["enabled"]:
                raise ValueError("所选大语言模型不存在或未启用")
            if strategy_id is None:
                cur = db.execute("INSERT INTO message_strategies(name,model_id,system_prompt) VALUES(?,?,?)", (name,model_id,prompt))
                strategy_id = int(cur.lastrowid)
            else:
                strategy_id = int(strategy_id)
                db.execute("UPDATE message_strategies SET name=?,model_id=?,system_prompt=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (name,model_id,prompt,strategy_id))
            db.execute("DELETE FROM message_strategy_knowledge_bases WHERE strategy_id=?", (strategy_id,))
            db.executemany(
                "INSERT INTO message_strategy_knowledge_bases(strategy_id,knowledge_base_id) VALUES(?,?)",
                [(strategy_id, base_id) for base_id in knowledge_base_ids],
            )
            return strategy_id

    def list(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows=db.execute("""SELECT s.*,m.display_name AS model_name,p.name AS provider_name
                FROM message_strategies s JOIN ai_models m ON m.id=s.model_id
                JOIN ai_providers p ON p.id=m.provider_id ORDER BY s.id DESC""").fetchall()
            bindings = db.execute(
                "SELECT strategy_id,knowledge_base_id FROM message_strategy_knowledge_bases ORDER BY knowledge_base_id"
            ).fetchall()
        by_strategy: dict[int, list[int]] = {}
        for binding in bindings:
            by_strategy.setdefault(int(binding["strategy_id"]), []).append(int(binding["knowledge_base_id"]))
        result = [dict(row) for row in rows]
        for item in result:
            item["knowledge_base_ids"] = by_strategy.get(int(item["id"]), [])
        return result

    def get(self, strategy_id: int) -> dict[str, Any] | None:
        with self._connect() as db:
            row=db.execute("SELECT * FROM message_strategies WHERE id=?",(int(strategy_id),)).fetchone()
            bindings=db.execute(
                "SELECT knowledge_base_id FROM message_strategy_knowledge_bases WHERE strategy_id=? ORDER BY knowledge_base_id",
                (int(strategy_id),),
            ).fetchall()
        if not row:return None
        result=dict(row); result["knowledge_base_ids"]=[int(item["knowledge_base_id"]) for item in bindings]
        return result

    def delete(self, strategy_id: int) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM message_strategies WHERE id=?",(int(strategy_id),))
