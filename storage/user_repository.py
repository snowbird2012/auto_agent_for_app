"""Temporary, unscreened user storage populated by collection tasks."""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator


class UserRepository:
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
                CREATE TABLE IF NOT EXISTS temporary_users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    handle TEXT NOT NULL,
                    handle_key TEXT NOT NULL UNIQUE,
                    username TEXT NOT NULL DEFAULT '',
                    following TEXT NOT NULL DEFAULT '未知',
                    followers TEXT NOT NULL DEFAULT '未知',
                    likes TEXT NOT NULL DEFAULT '未知',
                    mark TEXT NOT NULL DEFAULT '采集'
                        CHECK(mark IN ('采集','意向')),
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS temporary_user_comments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES temporary_users(id) ON DELETE CASCADE,
                    task_id INTEGER,
                    keyword TEXT NOT NULL DEFAULT '',
                    comment TEXT NOT NULL,
                    collected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, keyword, comment)
                );
                CREATE INDEX IF NOT EXISTS ix_temporary_users_mark ON temporary_users(mark);
                CREATE INDEX IF NOT EXISTS ix_temporary_comments_user ON temporary_user_comments(user_id, id);
                """
            )
            db.execute("DELETE FROM temporary_users WHERE handle_key IN ('', '@')")

    @staticmethod
    def _normalize_handle(value: str) -> tuple[str, str]:
        handle = value.strip()
        if handle and not handle.startswith("@"):
            handle = "@" + handle
        return handle, handle.casefold()

    @staticmethod
    def _tags(value: str) -> list[str]:
        try:
            result = json.loads(value or "[]")
        except (json.JSONDecodeError, TypeError):
            result = []
        return [str(item).strip() for item in result if str(item).strip()]

    def upsert_collected_user(self, record: dict[str, Any]) -> int | None:
        handle, handle_key = self._normalize_handle(str(record.get("handle", "")))
        if len(handle) <= 1 or handle in {"@未知", "@unknown"}:
            return None
        keyword = str(record.get("keyword", "")).strip()
        with self._connect() as db:
            row = db.execute(
                "SELECT id,tags_json FROM temporary_users WHERE handle_key=?", (handle_key,)
            ).fetchone()
            if row:
                user_id = int(row["id"])
                tags = self._tags(row["tags_json"])
                if keyword and keyword not in tags:
                    tags.append(keyword)
                db.execute(
                    """UPDATE temporary_users SET
                    handle=?,username=?,following=?,followers=?,likes=?,tags_json=?,
                    last_seen_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (
                        handle,
                        str(record.get("username", "")).strip(),
                        str(record.get("following", "未知")).strip() or "未知",
                        str(record.get("followers", "未知")).strip() or "未知",
                        str(record.get("likes", "未知")).strip() or "未知",
                        json.dumps(tags, ensure_ascii=False),
                        user_id,
                    ),
                )
            else:
                tags = [keyword] if keyword else []
                cursor = db.execute(
                    """INSERT INTO temporary_users
                    (handle,handle_key,username,following,followers,likes,mark,tags_json)
                    VALUES(?,?,?,?,?,?,'采集',?)""",
                    (
                        handle,
                        handle_key,
                        str(record.get("username", "")).strip(),
                        str(record.get("following", "未知")).strip() or "未知",
                        str(record.get("followers", "未知")).strip() or "未知",
                        str(record.get("likes", "未知")).strip() or "未知",
                        json.dumps(tags, ensure_ascii=False),
                    ),
                )
                user_id = int(cursor.lastrowid)
            comment = str(record.get("comment", "")).strip()
            if comment:
                db.execute(
                    """INSERT OR IGNORE INTO temporary_user_comments
                    (user_id,task_id,keyword,comment) VALUES(?,?,?,?)""",
                    (user_id, record.get("task_id"), keyword, comment),
                )
        return user_id

    def list_users(
        self,
        *,
        page: int = 1,
        page_size: int = 30,
        search: str = "",
        mark: str = "",
    ) -> tuple[list[dict[str, Any]], int]:
        page = max(1, int(page))
        page_size = max(1, min(200, int(page_size)))
        clauses: list[str] = []
        parameters: list[Any] = []
        if search.strip():
            pattern = f"%{search.strip()}%"
            clauses.append("(u.username LIKE ? OR u.handle LIKE ? OR u.tags_json LIKE ?)")
            parameters.extend([pattern, pattern, pattern])
        if mark in {"采集", "意向"}:
            clauses.append("u.mark=?")
            parameters.append(mark)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as db:
            total = int(db.execute(
                f"SELECT COUNT(*) FROM temporary_users u {where}", parameters
            ).fetchone()[0])
            rows = db.execute(
                f"""SELECT u.*,
                (SELECT COUNT(*) FROM temporary_user_comments c WHERE c.user_id=u.id) AS comment_count
                FROM temporary_users u {where}
                ORDER BY u.last_seen_at DESC,u.id DESC LIMIT ? OFFSET ?""",
                [*parameters, page_size, (page - 1) * page_size],
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["tags"] = self._tags(item.pop("tags_json"))
            result.append(item)
        return result, total

    def list_comments(self, user_id: int, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM temporary_user_comments WHERE user_id=?
                ORDER BY id DESC LIMIT ?""",
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_mark(self, user_id: int, mark: str) -> None:
        if mark not in {"采集", "意向"}:
            raise ValueError("标记只能是采集或意向")
        with self._connect() as db:
            db.execute("UPDATE temporary_users SET mark=? WHERE id=?", (mark, user_id))

    def delete_users(self, user_ids: list[int]) -> int:
        ids = sorted({int(item) for item in user_ids})
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as db:
            cursor = db.execute(
                f"DELETE FROM temporary_users WHERE id IN ({placeholders})", ids
            )
            return int(cursor.rowcount)

    def update_users_mark(self, user_ids: list[int], mark: str) -> int:
        if mark not in {"采集", "意向"}:
            raise ValueError("标记只能是采集或意向")
        ids = sorted({int(item) for item in user_ids})
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as db:
            cursor = db.execute(
                f"UPDATE temporary_users SET mark=? WHERE id IN ({placeholders})",
                [mark, *ids],
            )
            return int(cursor.rowcount)

    def list_collected_users_for_intent(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM temporary_users WHERE mark='采集' ORDER BY id"
            ).fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                user = dict(row)
                user["tags"] = self._tags(user.pop("tags_json"))
                comments = db.execute(
                    """SELECT keyword,comment,collected_at FROM temporary_user_comments
                    WHERE user_id=? ORDER BY id""",
                    (user["id"],),
                ).fetchall()
                user["comments"] = [dict(item) for item in comments]
                result.append(user)
        return result

    def apply_intent_results(
        self,
        intent_user_ids: list[int],
        non_intent_user_ids: list[int],
    ) -> tuple[int, int]:
        intent_ids = sorted({int(item) for item in intent_user_ids})
        non_intent_ids = sorted({int(item) for item in non_intent_user_ids})
        if set(intent_ids) & set(non_intent_ids):
            raise ValueError("意向与非意向用户集合不能重叠")
        with self._connect() as db:
            marked = 0
            deleted = 0
            if intent_ids:
                placeholders = ",".join("?" for _ in intent_ids)
                cursor = db.execute(
                    f"""UPDATE temporary_users SET mark='意向'
                    WHERE mark='采集' AND id IN ({placeholders})""",
                    intent_ids,
                )
                marked = int(cursor.rowcount)
            if non_intent_ids:
                placeholders = ",".join("?" for _ in non_intent_ids)
                cursor = db.execute(
                    f"""DELETE FROM temporary_users
                    WHERE mark='采集' AND id IN ({placeholders})""",
                    non_intent_ids,
                )
                deleted = int(cursor.rowcount)
        return marked, deleted
