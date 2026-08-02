"""SQLite repository for system settings, AI providers and model registry."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

from storage.secret_store import protect_secret, unprotect_secret


MODEL_TYPES = ("llm", "embedding", "rerank", "vision")


class SettingsRepository:
    def __init__(self, database_path: str | Path | None = None, seed: bool = True) -> None:
        project_root = Path(__file__).resolve().parents[1]
        self.database_path = Path(database_path) if database_path else project_root / "data" / "autoagent.db"
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        if seed:
            self._seed_defaults()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS ai_providers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    api_protocol TEXT NOT NULL DEFAULT 'openai_compatible',
                    base_url TEXT NOT NULL DEFAULT '',
                    api_key_secret TEXT NOT NULL DEFAULT '',
                    organization TEXT NOT NULL DEFAULT '',
                    timeout_seconds INTEGER NOT NULL DEFAULT 45,
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS ai_models (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider_id INTEGER NOT NULL REFERENCES ai_providers(id) ON DELETE CASCADE,
                    display_name TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    model_type TEXT NOT NULL CHECK(model_type IN ('llm','embedding','rerank','vision')),
                    context_length INTEGER,
                    vector_dimension INTEGER,
                    temperature REAL NOT NULL DEFAULT 0.3,
                    extra_json TEXT NOT NULL DEFAULT '{}',
                    is_default INTEGER NOT NULL DEFAULT 0 CHECK(is_default IN (0, 1)),
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(provider_id, model_id, model_type)
                );

                CREATE UNIQUE INDEX IF NOT EXISTS ux_ai_models_default_type
                ON ai_models(model_type) WHERE is_default = 1;

                CREATE TABLE IF NOT EXISTS app_settings (
                    setting_key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS proxy_settings (
                    id INTEGER PRIMARY KEY CHECK(id = 1),
                    enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0, 1)),
                    proxy_url TEXT NOT NULL DEFAULT '',
                    username TEXT NOT NULL DEFAULT '',
                    password_secret TEXT NOT NULL DEFAULT '',
                    use_for_model INTEGER NOT NULL DEFAULT 1 CHECK(use_for_model IN (0, 1)),
                    use_for_internal INTEGER NOT NULL DEFAULT 1 CHECK(use_for_internal IN (0, 1)),
                    bypass_hosts TEXT NOT NULL DEFAULT 'localhost,127.0.0.1,::1',
                    verify_ssl INTEGER NOT NULL DEFAULT 1 CHECK(verify_ssl IN (0, 1)),
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            # Seed-data migration: DeepSeek retired the legacy alias in 2026.
            db.execute(
                """UPDATE ai_models SET display_name='DeepSeek V4 Flash',
                model_id='deepseek-v4-flash', updated_at=CURRENT_TIMESTAMP
                WHERE display_name='DeepSeek Chat' AND model_id='deepseek-chat'"""
            )

    def _seed_defaults(self) -> None:
        if self.list_providers():
            return
        openai_id = self.save_provider({
            "name": "OpenAI",
            "api_protocol": "openai_compatible",
            "base_url": "https://api.openai.com/v1",
            "api_key": "",
            "organization": "",
            "timeout_seconds": 45,
            "enabled": False,
        })
        deepseek_id = self.save_provider({
            "name": "DeepSeek",
            "api_protocol": "openai_compatible",
            "base_url": "https://api.deepseek.com",
            "api_key": "",
            "organization": "",
            "timeout_seconds": 45,
            "enabled": False,
        })
        bailian_id = self.save_provider({
            "name": "阿里云百炼",
            "api_protocol": "openai_compatible",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "",
            "organization": "",
            "timeout_seconds": 45,
            "enabled": False,
        })
        self.save_model({"provider_id": openai_id, "display_name": "GPT 对话模型", "model_id": "gpt-5-mini", "model_type": "llm", "context_length": None, "vector_dimension": None, "temperature": 0.3, "extra_json": {}, "is_default": True, "enabled": True})
        self.save_model({"provider_id": openai_id, "display_name": "OpenAI 向量模型", "model_id": "text-embedding-3-large", "model_type": "embedding", "context_length": None, "vector_dimension": 3072, "temperature": 0, "extra_json": {}, "is_default": True, "enabled": True})
        self.save_model({"provider_id": deepseek_id, "display_name": "DeepSeek V4 Flash", "model_id": "deepseek-v4-flash", "model_type": "llm", "context_length": None, "vector_dimension": None, "temperature": 0.3, "extra_json": {}, "is_default": False, "enabled": True})
        self.save_model({"provider_id": bailian_id, "display_name": "视觉理解模型", "model_id": "qwen-vl-max", "model_type": "vision", "context_length": None, "vector_dimension": None, "temperature": 0.2, "extra_json": {}, "is_default": True, "enabled": True})
        self.save_model({"provider_id": bailian_id, "display_name": "文本排序模型", "model_id": "qwen3-rerank", "model_type": "rerank", "context_length": None, "vector_dimension": None, "temperature": 0, "extra_json": {}, "is_default": True, "enabled": True})

    def list_providers(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM ai_providers ORDER BY name COLLATE NOCASE").fetchall()
        return [self._provider_row(row, reveal_key=False) for row in rows]

    def get_provider(self, provider_id: int, reveal_key: bool = False) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM ai_providers WHERE id = ?", (provider_id,)).fetchone()
        return self._provider_row(row, reveal_key) if row else None

    @staticmethod
    def _provider_row(row: sqlite3.Row, reveal_key: bool) -> dict[str, Any]:
        result = dict(row)
        secret = result.pop("api_key_secret", "")
        result["has_api_key"] = bool(secret)
        result["api_key"] = unprotect_secret(secret) if reveal_key else ""
        result["enabled"] = bool(result["enabled"])
        return result

    def save_provider(self, values: dict[str, Any], provider_id: int | None = None) -> int:
        name = str(values.get("name", "")).strip()
        if not name:
            raise ValueError("厂家名称不能为空")
        api_key = str(values.get("api_key", ""))
        with self._connect() as db:
            if provider_id is None:
                cursor = db.execute(
                    """INSERT INTO ai_providers
                    (name, api_protocol, base_url, api_key_secret, organization, timeout_seconds, enabled)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (name, values.get("api_protocol", "openai_compatible"), str(values.get("base_url", "")).strip(), protect_secret(api_key), str(values.get("organization", "")).strip(), int(values.get("timeout_seconds", 45)), int(bool(values.get("enabled", True)))),
                )
                return int(cursor.lastrowid)
            existing = db.execute("SELECT api_key_secret FROM ai_providers WHERE id = ?", (provider_id,)).fetchone()
            if not existing:
                raise ValueError("厂家配置不存在")
            secret = protect_secret(api_key) if api_key else existing["api_key_secret"]
            db.execute(
                """UPDATE ai_providers SET name=?, api_protocol=?, base_url=?, api_key_secret=?,
                organization=?, timeout_seconds=?, enabled=?, updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (name, values.get("api_protocol", "openai_compatible"), str(values.get("base_url", "")).strip(), secret, str(values.get("organization", "")).strip(), int(values.get("timeout_seconds", 45)), int(bool(values.get("enabled", True))), provider_id),
            )
            return provider_id

    def delete_provider(self, provider_id: int) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM ai_providers WHERE id = ?", (provider_id,))

    def list_models(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT m.*, p.name AS provider_name FROM ai_models m
                JOIN ai_providers p ON p.id=m.provider_id
                ORDER BY CASE m.model_type WHEN 'llm' THEN 1 WHEN 'embedding' THEN 2
                WHEN 'rerank' THEN 3 ELSE 4 END, m.display_name"""
            ).fetchall()
        return [self._model_row(row) for row in rows]

    def get_model(self, model_id: int) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM ai_models WHERE id = ?", (model_id,)).fetchone()
        return self._model_row(row) if row else None

    @staticmethod
    def _model_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["enabled"] = bool(result["enabled"])
        result["is_default"] = bool(result["is_default"])
        try:
            result["extra_json"] = json.loads(result.get("extra_json") or "{}")
        except json.JSONDecodeError:
            result["extra_json"] = {}
        return result

    def save_model(self, values: dict[str, Any], record_id: int | None = None) -> int:
        model_type = str(values.get("model_type", ""))
        if model_type not in MODEL_TYPES:
            raise ValueError("无效的模型类型")
        display_name = str(values.get("display_name", "")).strip()
        api_model_id = str(values.get("model_id", "")).strip()
        if not display_name or not api_model_id:
            raise ValueError("显示名称和 API 模型 ID 不能为空")
        provider_id = int(values["provider_id"])
        is_default = bool(values.get("is_default", False))
        payload = (
            provider_id, display_name, api_model_id, model_type,
            values.get("context_length") or None, values.get("vector_dimension") or None,
            float(values.get("temperature", 0.3)),
            json.dumps(values.get("extra_json") or {}, ensure_ascii=False),
            int(is_default), int(bool(values.get("enabled", True))),
        )
        with self._connect() as db:
            if is_default:
                db.execute("UPDATE ai_models SET is_default=0, updated_at=CURRENT_TIMESTAMP WHERE model_type=?", (model_type,))
            if record_id is None:
                cursor = db.execute(
                    """INSERT INTO ai_models
                    (provider_id,display_name,model_id,model_type,context_length,vector_dimension,temperature,extra_json,is_default,enabled)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""", payload,
                )
                return int(cursor.lastrowid)
            db.execute(
                """UPDATE ai_models SET provider_id=?,display_name=?,model_id=?,model_type=?,
                context_length=?,vector_dimension=?,temperature=?,extra_json=?,is_default=?,enabled=?,
                updated_at=CURRENT_TIMESTAMP WHERE id=?""", payload + (record_id,),
            )
            return record_id

    def delete_model(self, model_id: int) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM ai_models WHERE id = ?", (model_id,))

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self._connect() as db:
            row = db.execute("SELECT value_json FROM app_settings WHERE setting_key=?", (key,)).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value_json"])
        except json.JSONDecodeError:
            return default

    def set_setting(self, key: str, value: Any) -> None:
        encoded = json.dumps(value, ensure_ascii=False)
        with self._connect() as db:
            db.execute(
                """INSERT INTO app_settings(setting_key,value_json) VALUES(?,?)
                ON CONFLICT(setting_key) DO UPDATE SET value_json=excluded.value_json,
                updated_at=CURRENT_TIMESTAMP""", (key, encoded),
            )

    def get_proxy_settings(self, reveal_password: bool = False) -> dict[str, Any]:
        defaults = {
            "enabled": False,
            "proxy_url": "",
            "username": "",
            "password": "",
            "has_password": False,
            "use_for_model": True,
            "use_for_internal": True,
            "bypass_hosts": "localhost,127.0.0.1,::1",
            "verify_ssl": True,
        }
        with self._connect() as db:
            row = db.execute("SELECT * FROM proxy_settings WHERE id=1").fetchone()
        if not row:
            return defaults
        values = dict(row)
        secret = values.pop("password_secret", "")
        values.pop("id", None)
        values.pop("updated_at", None)
        for key in ("enabled", "use_for_model", "use_for_internal", "verify_ssl"):
            values[key] = bool(values[key])
        values["has_password"] = bool(secret)
        values["password"] = unprotect_secret(secret) if reveal_password else ""
        return defaults | values

    def save_proxy_settings(self, values: dict[str, Any]) -> None:
        proxy_url = str(values.get("proxy_url", "")).strip()
        if values.get("enabled") and not proxy_url:
            raise ValueError("启用代理时必须填写代理地址")
        password = str(values.get("password", ""))
        with self._connect() as db:
            existing = db.execute("SELECT password_secret FROM proxy_settings WHERE id=1").fetchone()
            secret = protect_secret(password) if password else (existing["password_secret"] if existing else "")
            db.execute(
                """INSERT INTO proxy_settings
                (id,enabled,proxy_url,username,password_secret,use_for_model,use_for_internal,bypass_hosts,verify_ssl)
                VALUES(1,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET enabled=excluded.enabled,proxy_url=excluded.proxy_url,
                username=excluded.username,password_secret=excluded.password_secret,
                use_for_model=excluded.use_for_model,use_for_internal=excluded.use_for_internal,
                bypass_hosts=excluded.bypass_hosts,verify_ssl=excluded.verify_ssl,
                updated_at=CURRENT_TIMESTAMP""",
                (
                    int(bool(values.get("enabled", False))), proxy_url,
                    str(values.get("username", "")).strip(), secret,
                    int(bool(values.get("use_for_model", True))),
                    int(bool(values.get("use_for_internal", True))),
                    str(values.get("bypass_hosts", "")).strip(),
                    int(bool(values.get("verify_ssl", True))),
                ),
            )
