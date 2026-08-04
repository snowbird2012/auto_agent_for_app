"""Persistence and single-runner coordination for automation jobs."""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator


class AutomationJobRepository:
    def __init__(self, database_path: str | Path | None = None) -> None:
        root = Path(__file__).resolve().parents[1]
        self.database_path = (
            Path(database_path) if database_path else root / "data" / "autoagent.db"
        )
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
            db.rollback()
            raise
        finally:
            db.close()

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS automation_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_type TEXT NOT NULL DEFAULT 'dialog'
                        CHECK(job_type IN ('dialog')),
                    device_serial TEXT NOT NULL DEFAULT '',
                    device_name TEXT NOT NULL DEFAULT '',
                    strategy_id INTEGER NOT NULL DEFAULT 0,
                    strategy_name TEXT NOT NULL DEFAULT '',
                    user_tag TEXT NOT NULL,
                    opening_message TEXT NOT NULL,
                    execution_count INTEGER NOT NULL DEFAULT 1,
                    completed_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'created'
                        CHECK(status IN ('created','running','stopped','completed','failed')),
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    started_at TEXT,
                    finished_at TEXT
                );

                CREATE TABLE IF NOT EXISTS automation_job_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL REFERENCES automation_jobs(id) ON DELETE CASCADE,
                    level TEXT NOT NULL DEFAULT 'INFO',
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS ix_automation_job_logs
                    ON automation_job_logs(job_id,id);
                CREATE UNIQUE INDEX IF NOT EXISTS ux_automation_job_single_running
                    ON automation_jobs(status) WHERE status='running';
                """
            )
            columns = {
                str(row["name"])
                for row in db.execute("PRAGMA table_info(automation_jobs)").fetchall()
            }
            if "device_serial" not in columns:
                db.execute(
                    "ALTER TABLE automation_jobs "
                    "ADD COLUMN device_serial TEXT NOT NULL DEFAULT ''"
                )
            if "device_name" not in columns:
                db.execute(
                    "ALTER TABLE automation_jobs "
                    "ADD COLUMN device_name TEXT NOT NULL DEFAULT ''"
                )
            if "strategy_id" not in columns:
                db.execute("ALTER TABLE automation_jobs ADD COLUMN strategy_id INTEGER NOT NULL DEFAULT 0")
            if "strategy_name" not in columns:
                db.execute("ALTER TABLE automation_jobs ADD COLUMN strategy_name TEXT NOT NULL DEFAULT ''")

    def create_job(self, values: dict[str, Any]) -> int:
        job_type = str(values.get("job_type", "dialog")).strip()
        device_serial = str(values.get("device_serial", "")).strip()
        device_name = str(values.get("device_name", "")).strip()
        strategy_id = int(values.get("strategy_id") or 0)
        strategy_name = str(values.get("strategy_name", "")).strip()
        user_tag = str(values.get("user_tag", "")).strip()
        opening_message = str(values.get("opening_message", "")).strip()
        execution_count = max(1, min(10000, int(values.get("execution_count", 1))))
        if job_type != "dialog":
            raise ValueError("当前仅支持对话类型")
        if not device_serial:
            raise ValueError("请选择执行设备")
        if strategy_id <= 0:
            raise ValueError("请选择模型策略；如果策略列表为空，请先建立消息策略")
        if not user_tag:
            raise ValueError("请选择目标用户标签")
        if not opening_message:
            raise ValueError("启动话术不能为空")
        with self._connect() as db:
            cursor = db.execute(
                """INSERT INTO automation_jobs
                (job_type,device_serial,device_name,strategy_id,strategy_name,user_tag,opening_message,execution_count)
                VALUES(?,?,?,?,?,?,?,?)""",
                (
                    job_type, device_serial, device_name, strategy_id, strategy_name, user_tag,
                    opening_message, execution_count,
                ),
            )
            job_id = int(cursor.lastrowid)
            db.execute(
                "INSERT INTO automation_job_logs(job_id,message) VALUES(?,?)",
                (job_id, "自动化任务已创建"),
            )
        return job_id

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM automation_jobs ORDER BY id DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM automation_jobs WHERE id=?", (int(job_id),)
            ).fetchone()
        return dict(row) if row else None

    def start_job(self, job_id: int) -> None:
        with self._connect() as db:
            running = db.execute(
                "SELECT id FROM automation_jobs WHERE status='running' AND id<>?",
                (int(job_id),),
            ).fetchone()
            if running:
                raise ValueError(f"自动化任务 #{running['id']} 正在执行，请先停止该任务")
            row = db.execute(
                "SELECT status,device_serial FROM automation_jobs WHERE id=?",
                (int(job_id),),
            ).fetchone()
            if not row:
                raise ValueError("自动化任务不存在")
            if row["status"] == "running":
                raise ValueError("该自动化任务已经在执行")
            if not str(row["device_serial"] or "").strip():
                raise ValueError("该任务尚未选择执行设备，请重新创建任务")
            db.execute(
                """UPDATE automation_jobs SET status='running',error='',
                started_at=CURRENT_TIMESTAMP,finished_at=NULL,
                updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (int(job_id),),
            )
            db.execute(
                "INSERT INTO automation_job_logs(job_id,message) VALUES(?,?)",
                (int(job_id), "自动化任务已启动"),
            )

    def stop_job(self, job_id: int) -> None:
        with self._connect() as db:
            row = db.execute(
                "SELECT status FROM automation_jobs WHERE id=?", (int(job_id),)
            ).fetchone()
            if not row:
                raise ValueError("自动化任务不存在")
            if row["status"] != "running":
                raise ValueError("只有执行中的任务可以停止")
            db.execute(
                """UPDATE automation_jobs SET status='stopped',
                finished_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
                WHERE id=?""",
                (int(job_id),),
            )
            db.execute(
                "INSERT INTO automation_job_logs(job_id,message) VALUES(?,?)",
                (int(job_id), "自动化任务已停止"),
            )

    def execute_dialog_preview(self, job_id: int, keep_running: bool = False) -> int:
        """Simulate first-message delivery and persist the real selection result."""
        with self._connect() as db:
            job = db.execute(
                "SELECT * FROM automation_jobs WHERE id=?", (int(job_id),)
            ).fetchone()
            if not job:
                raise ValueError("自动化任务不存在")
            if job["status"] != "running":
                raise ValueError("只有执行中的任务可以处理用户")

            table = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='temporary_users'"
            ).fetchone()
            if not table:
                raise ValueError("用户库尚未初始化")
            user_columns = {
                str(row["name"])
                for row in db.execute("PRAGMA table_info(temporary_users)").fetchall()
            }
            if "first_message_sent" not in user_columns:
                raise ValueError("用户库缺少首次消息字段，请重新打开应用完成升级")

            requested = int(job["execution_count"])
            target_tag = str(job["user_tag"])
            rows = db.execute(
                """SELECT id,username,handle,tags_json FROM temporary_users
                WHERE first_message_sent=0 ORDER BY last_seen_at DESC,id DESC"""
            ).fetchall()
            selected: list[sqlite3.Row] = []
            target_key = target_tag.casefold()
            for row in rows:
                try:
                    tags = json.loads(row["tags_json"] or "[]")
                except (json.JSONDecodeError, TypeError):
                    tags = []
                if any(str(tag).strip().casefold() == target_key for tag in tags):
                    selected.append(row)
                    if len(selected) >= requested:
                        break

            device_text = str(job["device_name"] or job["device_serial"])
            db.execute(
                "INSERT INTO automation_job_logs(job_id,message) VALUES(?,?)",
                (
                    int(job_id),
                    f"执行设备：{device_text}（{job['device_serial']}）；"
                    f"标签：{target_tag}；计划发送：{requested} 位",
                ),
            )
            for index, user in enumerate(selected, 1):
                display = str(user["handle"] or user["username"] or user["id"])
                db.execute(
                    "INSERT INTO automation_job_logs(job_id,message) VALUES(?,?)",
                    (
                        int(job_id),
                        f"[{index}/{requested}] 模拟向 {display} 发送首次消息："
                        f"{job['opening_message']}",
                    ),
                )
                db.execute(
                    "UPDATE temporary_users SET first_message_sent=1 WHERE id=?",
                    (int(user["id"]),),
                )

            completed = len(selected)
            if completed < requested:
                summary = (
                    f"符合条件的未发送用户仅 {completed} 位，"
                    f"少于计划的 {requested} 位；本次模拟执行结束"
                )
            else:
                summary = f"本次模拟执行完成，共处理 {completed} 位用户"
            db.execute(
                "INSERT INTO automation_job_logs(job_id,message) VALUES(?,?)",
                (int(job_id), summary),
            )
            if keep_running:
                db.execute(
                    """UPDATE automation_jobs SET completed_count=?,
                    finished_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (completed, int(job_id)),
                )
            else:
                db.execute(
                    """UPDATE automation_jobs SET status='completed',completed_count=?,
                    finished_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (completed, int(job_id)),
                )
        return completed

    def fail_job(self, job_id: int, error: str) -> None:
        message = str(error).strip() or "未知错误"
        with self._connect() as db:
            db.execute(
                """UPDATE automation_jobs SET status='failed',error=?,
                finished_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (message, int(job_id)),
            )
            db.execute(
                "INSERT INTO automation_job_logs(job_id,level,message) VALUES(?,'ERROR',?)",
                (int(job_id), f"执行失败：{message}"),
            )

    def add_log(self, job_id: int, message: str, level: str = "INFO") -> None:
        level = str(level).strip().upper() or "INFO"
        with self._connect() as db:
            if not db.execute(
                "SELECT 1 FROM automation_jobs WHERE id=?", (int(job_id),)
            ).fetchone():
                raise ValueError("自动化任务不存在")
            db.execute(
                "INSERT INTO automation_job_logs(job_id,level,message) VALUES(?,?,?)",
                (int(job_id), level, str(message).strip()),
            )

    def delete_job(self, job_id: int) -> None:
        with self._connect() as db:
            row = db.execute(
                "SELECT status FROM automation_jobs WHERE id=?", (int(job_id),)
            ).fetchone()
            if row and row["status"] == "running":
                raise ValueError("执行中的自动化任务不能删除")
            db.execute("DELETE FROM automation_jobs WHERE id=?", (int(job_id),))

    def list_logs(self, job_id: int, limit: int = 300) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM automation_job_logs WHERE job_id=?
                ORDER BY id DESC LIMIT ?""",
                (int(job_id), max(1, min(2000, int(limit)))),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]
