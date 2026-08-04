"""Persistent contacts and direct-message history."""
from __future__ import annotations
from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Any, Iterator

class ConversationRepository:
    def __init__(self,database_path:str|Path|None=None):
        root=Path(__file__).resolve().parents[1]; self.database_path=Path(database_path) if database_path else root/"data"/"autoagent.db"; self._initialize()
    @contextmanager
    def _connect(self)->Iterator[sqlite3.Connection]:
        db=sqlite3.connect(self.database_path); db.row_factory=sqlite3.Row
        try: yield db; db.commit()
        except Exception: db.rollback(); raise
        finally: db.close()
    def _initialize(self):
        with self._connect() as db: db.executescript("""
        CREATE TABLE IF NOT EXISTS message_contacts(
          id INTEGER PRIMARY KEY AUTOINCREMENT,identity_key TEXT NOT NULL UNIQUE,
          handle TEXT NOT NULL DEFAULT '',display_name TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS direct_messages(
          id INTEGER PRIMARY KEY AUTOINCREMENT,contact_id INTEGER NOT NULL REFERENCES message_contacts(id) ON DELETE CASCADE,
          job_id INTEGER,direction TEXT NOT NULL CHECK(direction IN ('inbound','outbound')),
          message_kind TEXT NOT NULL DEFAULT 'message',content TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE INDEX IF NOT EXISTS ix_direct_messages_contact ON direct_messages(contact_id,id);
        """)
    def record(self,values:dict[str,Any])->int:
        handle=str(values.get("handle","")).strip(); name=str(values.get("display_name","")).strip()
        if handle and not handle.startswith("@"):handle="@"+handle
        key=("handle:"+handle.casefold()) if handle else ("name:"+name.casefold())
        content=str(values.get("content","")).strip(); direction=str(values.get("direction", ""))
        if not key.split(":",1)[1]:raise ValueError("消息联系人不能为空")
        if direction not in {"inbound","outbound"}:raise ValueError("消息方向无效")
        if not content:raise ValueError("消息内容不能为空")
        with self._connect() as db:
            row=db.execute("SELECT id FROM message_contacts WHERE identity_key=?",(key,)).fetchone()
            if row: contact_id=int(row["id"]); db.execute("UPDATE message_contacts SET handle=CASE WHEN ?<>'' THEN ? ELSE handle END,display_name=CASE WHEN ?<>'' THEN ? ELSE display_name END,updated_at=CURRENT_TIMESTAMP WHERE id=?",(handle,handle,name,name,contact_id))
            else: contact_id=int(db.execute("INSERT INTO message_contacts(identity_key,handle,display_name) VALUES(?,?,?)",(key,handle,name or handle)).lastrowid)
            cur=db.execute("INSERT INTO direct_messages(contact_id,job_id,direction,message_kind,content) VALUES(?,?,?,?,?)",(contact_id,values.get("job_id"),direction,str(values.get("message_kind","message")),content))
            return int(cur.lastrowid)

    def record_incoming_batch(
        self,
        *,
        handle: str = "",
        display_name: str = "",
        messages: list[dict[str, Any]],
        job_id: int | None = None,
    ) -> list[dict[str, str]]:
        """Persist only the new suffix of a chronological incoming batch.

        TikTok does not expose stable message ids through accessibility.  The
        listener therefore sends the complete incoming run after our latest
        outbound message.  Comparing that run with the already stored run
        preserves repeated messages (for example two consecutive ``你好``)
        while making repeated UI scans idempotent.
        """
        normalized: list[dict[str, str]] = []
        for item in messages:
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            normalized.append({
                "type": str(item.get("type", "text") or "text"),
                "content": content,
            })
        if not normalized:
            return []

        handle = str(handle).strip()
        name = str(display_name).strip()
        if handle and not handle.startswith("@"):
            handle = "@" + handle
        key = ("handle:" + handle.casefold()) if handle else ("name:" + name.casefold())
        if not key.split(":", 1)[1]:
            raise ValueError("消息联系人不能为空")

        with self._connect() as db:
            row = db.execute(
                "SELECT id FROM message_contacts WHERE identity_key=?", (key,)
            ).fetchone()
            if row:
                contact_id = int(row["id"])
                db.execute(
                    """UPDATE message_contacts
                       SET handle=CASE WHEN ?<>'' THEN ? ELSE handle END,
                           display_name=CASE WHEN ?<>'' THEN ? ELSE display_name END,
                           updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (handle, handle, name, name, contact_id),
                )
            else:
                contact_id = int(db.execute(
                    "INSERT INTO message_contacts(identity_key,handle,display_name) VALUES(?,?,?)",
                    (key, handle, name or handle),
                ).lastrowid)

            last_outbound = db.execute(
                "SELECT COALESCE(MAX(id),0) id FROM direct_messages "
                "WHERE contact_id=? AND direction='outbound'",
                (contact_id,),
            ).fetchone()
            stored_rows = db.execute(
                """SELECT message_kind,content FROM direct_messages
                   WHERE contact_id=? AND direction='inbound' AND id>?
                   ORDER BY id""",
                (contact_id, int(last_outbound["id"])),
            ).fetchall()
            stored = [
                (self._incoming_type(row["message_kind"]), str(row["content"]))
                for row in stored_rows
            ]
            current = [(item["type"], item["content"]) for item in normalized]
            overlap = 0
            for size in range(min(len(stored), len(current)), 0, -1):
                if stored[-size:] == current[:size]:
                    overlap = size
                    break
            new_messages = normalized[overlap:]
            for item in new_messages:
                db.execute(
                    """INSERT INTO direct_messages
                       (contact_id,job_id,direction,message_kind,content)
                       VALUES(?,?,'inbound',?,?)""",
                    (
                        contact_id,
                        job_id,
                        "received_" + item["type"],
                        item["content"],
                    ),
                )
            if new_messages:
                db.execute(
                    "UPDATE message_contacts SET updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (contact_id,),
                )
            return new_messages

    @staticmethod
    def _incoming_type(message_kind: str) -> str:
        value = str(message_kind)
        return value.removeprefix("received_") if value.startswith("received_") else "text"
    def list_contacts(self,search:str=""):
        pattern=f"%{search.strip()}%"
        with self._connect() as db: rows=db.execute("""SELECT c.*,
          (SELECT content FROM direct_messages m WHERE m.contact_id=c.id ORDER BY m.id DESC LIMIT 1) latest_message,
          (SELECT created_at FROM direct_messages m WHERE m.contact_id=c.id ORDER BY m.id DESC LIMIT 1) latest_at,
          (SELECT COUNT(*) FROM direct_messages m WHERE m.contact_id=c.id) message_count
          FROM message_contacts c WHERE ?='' OR c.handle LIKE ? OR c.display_name LIKE ? OR EXISTS(
          SELECT 1 FROM direct_messages m WHERE m.contact_id=c.id AND m.content LIKE ?)
          ORDER BY latest_at DESC,c.id DESC""",(search.strip(),pattern,pattern,pattern)).fetchall()
        return [dict(r) for r in rows]
    def list_messages(self,contact_id:int):
        with self._connect() as db: rows=db.execute("SELECT * FROM direct_messages WHERE contact_id=? ORDER BY id",(int(contact_id),)).fetchall()
        return [dict(r) for r in rows]
