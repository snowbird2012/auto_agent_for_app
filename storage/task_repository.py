"""SQLite persistence for automation tasks and execution logs."""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator


class TaskRepository:
    def __init__(self, database_path: str | Path | None = None) -> None:
        root = Path(__file__).resolve().parents[1]
        self.database_path = Path(database_path) if database_path else root / "data" / "autoagent.db"
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
                CREATE TABLE IF NOT EXISTS automation_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    device_serial TEXT NOT NULL,
                    app_package TEXT NOT NULL DEFAULT 'com.zhiliaoapp.musically',
                    keywords_json TEXT NOT NULL,
                    content_type TEXT NOT NULL DEFAULT 'video'
                        CHECK(content_type IN ('video','live','either')),
                    max_comments INTEGER NOT NULL DEFAULT 20,
                    collection_minutes INTEGER NOT NULL DEFAULT 2,
                    status TEXT NOT NULL DEFAULT 'created',
                    current_keyword TEXT NOT NULL DEFAULT '',
                    current_step TEXT NOT NULL DEFAULT 'CREATED',
                    progress INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    started_at TEXT,
                    finished_at TEXT
                );

                CREATE TABLE IF NOT EXISTS task_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL REFERENCES automation_tasks(id) ON DELETE CASCADE,
                    level TEXT NOT NULL DEFAULT 'INFO',
                    step TEXT NOT NULL DEFAULT '',
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS ix_task_logs_task_id ON task_logs(task_id, id);

                CREATE TABLE IF NOT EXISTS collected_rooms (
                    content_type TEXT NOT NULL CHECK(content_type IN ('video','live')),
                    content_key TEXT NOT NULL,
                    task_id INTEGER REFERENCES automation_tasks(id) ON DELETE CASCADE,
                    room_title TEXT NOT NULL DEFAULT '',
                    keyword TEXT NOT NULL DEFAULT '',
                    last_collected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(content_type, content_key)
                );
                CREATE INDEX IF NOT EXISTS ix_collected_rooms_time
                    ON collected_rooms(last_collected_at);
                """
            )
            columns = {
                row["name"] for row in db.execute("PRAGMA table_info(automation_tasks)").fetchall()
            }
            if "max_comments" not in columns:
                db.execute(
                    "ALTER TABLE automation_tasks ADD COLUMN max_comments INTEGER NOT NULL DEFAULT 20"
                )
            if "collection_minutes" not in columns:
                db.execute(
                    "ALTER TABLE automation_tasks ADD COLUMN collection_minutes INTEGER NOT NULL DEFAULT 2"
                )
            room_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(collected_rooms)").fetchall()
            }
            if "task_id" not in room_columns:
                db.execute(
                    "ALTER TABLE collected_rooms ADD COLUMN task_id INTEGER REFERENCES automation_tasks(id) ON DELETE CASCADE"
                )
            if "room_title" not in room_columns:
                db.execute(
                    "ALTER TABLE collected_rooms ADD COLUMN room_title TEXT NOT NULL DEFAULT ''"
                )
            db.execute(
                "CREATE INDEX IF NOT EXISTS ix_collected_rooms_task ON collected_rooms(task_id,last_collected_at)"
            )
            # Associate records created before task ownership was introduced
            # with the newest matching task, so they remain visible in the UI.
            db.execute(
                """UPDATE collected_rooms AS room SET task_id=(
                    SELECT task.id FROM automation_tasks AS task
                    WHERE (task.content_type=room.content_type OR task.content_type='either')
                    AND task.keywords_json LIKE '%' || room.keyword || '%'
                    ORDER BY task.id DESC LIMIT 1
                ) WHERE room.task_id IS NULL"""
            )

    def create_task(self, values: dict[str, Any]) -> int:
        name = str(values.get("name", "")).strip()
        serial = str(values.get("device_serial", "")).strip()
        keywords = [str(item).strip() for item in values.get("keywords", []) if str(item).strip()]
        content_type = str(values.get("content_type", "video"))
        if not name:
            raise ValueError("任务名称不能为空")
        if not serial:
            raise ValueError("请选择执行设备")
        if not keywords:
            raise ValueError("至少输入一个搜索关键词")
        if content_type not in {"video", "live", "either"}:
            raise ValueError("内容类型无效")
        with self._connect() as db:
            cursor = db.execute(
                """INSERT INTO automation_tasks
                (name,device_serial,app_package,keywords_json,content_type,max_comments,collection_minutes)
                VALUES(?,?,?,?,?,?,?)""",
                (
                    name,
                    serial,
                    values.get("app_package", "com.zhiliaoapp.musically"),
                    json.dumps(keywords, ensure_ascii=False),
                    content_type,
                    max(1, min(200, int(values.get("max_comments", 20)))),
                    max(1, min(1440, int(values.get("collection_minutes", 2)))),
                ),
            )
            task_id = int(cursor.lastrowid)
        self.add_log(task_id, "INFO", "CREATED", "任务已创建")
        return task_id

    def list_tasks(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM automation_tasks ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [self._task_row(row) for row in rows]

    def get_task(self, task_id: int) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM automation_tasks WHERE id=?", (task_id,)).fetchone()
        return self._task_row(row) if row else None

    @staticmethod
    def _task_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        try:
            result["keywords"] = json.loads(result.pop("keywords_json"))
        except (json.JSONDecodeError, TypeError):
            result["keywords"] = []
        return result

    def update_runtime(self, task_id: int, *, status: str | None = None, step: str | None = None, progress: int | None = None, keyword: str | None = None, error: str | None = None) -> None:
        updates = ["updated_at=CURRENT_TIMESTAMP"]
        parameters: list[Any] = []
        for column, value in (("status", status), ("current_step", step), ("progress", progress), ("current_keyword", keyword), ("error", error)):
            if value is not None:
                updates.append(f"{column}=?")
                parameters.append(value)
        if status == "running":
            updates.append("started_at=COALESCE(started_at,CURRENT_TIMESTAMP)")
            updates.append("finished_at=NULL")
        elif status in {"completed", "failed", "cancelled"}:
            updates.append("finished_at=CURRENT_TIMESTAMP")
        parameters.append(task_id)
        with self._connect() as db:
            db.execute(f"UPDATE automation_tasks SET {','.join(updates)} WHERE id=?", parameters)

    def add_log(self, task_id: int, level: str, step: str, message: str) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO task_logs(task_id,level,step,message) VALUES(?,?,?,?)", (task_id, level, step, message))

    def list_logs(self, task_id: int, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM task_logs WHERE task_id=? ORDER BY id DESC LIMIT ?", (task_id, limit)
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def delete_task(self, task_id: int) -> None:
        with self._connect() as db:
            row = db.execute("SELECT status FROM automation_tasks WHERE id=?", (task_id,)).fetchone()
            if row and row["status"] == "running":
                raise ValueError("运行中的任务不能删除")
            db.execute("DELETE FROM automation_tasks WHERE id=?", (task_id,))

    def was_room_collected_recently(
        self, content_type: str, content_key: str, hours: int = 24
    ) -> bool:
        """Return whether this content was successfully collected in the window."""
        if content_type not in {"video", "live"} or not content_key.strip():
            return False
        hours = max(1, min(24 * 365, int(hours)))
        with self._connect() as db:
            row = db.execute(
                """SELECT 1 FROM collected_rooms
                WHERE content_type=? AND content_key=?
                AND last_collected_at >= datetime('now', ?)""",
                (content_type, content_key.strip(), f"-{hours} hours"),
            ).fetchone()
        return row is not None

    def record_room_collected(
        self,
        content_type: str,
        content_key: str,
        keyword: str = "",
        room_title: str = "",
        task_id: int | None = None,
    ) -> None:
        if content_type not in {"video", "live"} or not content_key.strip():
            return
        with self._connect() as db:
            db.execute(
                """INSERT INTO collected_rooms
                (content_type,content_key,task_id,room_title,keyword,last_collected_at)
                VALUES(?,?,?,?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(content_type,content_key) DO UPDATE SET
                    task_id=excluded.task_id,
                    room_title=excluded.room_title,
                    keyword=excluded.keyword,
                    last_collected_at=CURRENT_TIMESTAMP""",
                (
                    content_type,
                    content_key.strip(),
                    task_id,
                    room_title.strip(),
                    keyword.strip(),
                ),
            )

    def list_collected_rooms(self, task_id: int) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT rowid AS id,content_type,content_key,task_id,
                room_title,keyword,last_collected_at
                FROM collected_rooms WHERE task_id=?
                ORDER BY last_collected_at DESC,rowid DESC""",
                (int(task_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_collected_rooms(self, room_ids: list[int], task_id: int) -> int:
        ids = sorted({int(item) for item in room_ids})
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as db:
            cursor = db.execute(
                f"DELETE FROM collected_rooms WHERE task_id=? AND rowid IN ({placeholders})",
                [int(task_id), *ids],
            )
            return int(cursor.rowcount)

    def dashboard_task_data(
        self, start_utc: str, end_utc: str, activity_limit: int = 6
    ) -> dict[str, Any]:
        """Return real task/room counters and latest persisted task events."""
        with self._connect() as db:
            status_rows = db.execute(
                "SELECT status,COUNT(*) AS count FROM automation_tasks GROUP BY status"
            ).fetchall()
            finished_rows = db.execute(
                """SELECT status,COUNT(*) AS count FROM automation_tasks
                WHERE finished_at>=? AND finished_at<? GROUP BY status""",
                (start_utc, end_utc),
            ).fetchall()
            room_rows = db.execute(
                """SELECT content_type,COUNT(*) AS count FROM collected_rooms
                WHERE last_collected_at>=? AND last_collected_at<?
                GROUP BY content_type""",
                (start_utc, end_utc),
            ).fetchall()
            activities = db.execute(
                """SELECT log.level,log.step,log.message,log.created_at,
                task.id AS task_id,task.name AS task_name
                FROM task_logs AS log
                JOIN automation_tasks AS task ON task.id=log.task_id
                ORDER BY log.id DESC LIMIT ?""",
                (max(1, min(30, int(activity_limit))),),
            ).fetchall()
        return {
            "statuses": {row["status"]: int(row["count"]) for row in status_rows},
            "finished_today": {
                row["status"]: int(row["count"]) for row in finished_rows
            },
            "rooms_today": {
                row["content_type"]: int(row["count"]) for row in room_rows
            },
            "activities": [dict(row) for row in activities],
        }
