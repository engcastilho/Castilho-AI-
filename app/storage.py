"""Persistência das conversas em SQLite.

O histórico é guardado exatamente como foi enviado à API (incluindo os blocos de
"thinking" das respostas), porque os modelos atuais exigem que o histórico seja
apenas acrescentado, nunca reescrito: o prompt de sistema é congelado na criação
da conversa e as mensagens nunca são editadas.
"""

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    system_prompt TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, id);
"""


class Storage:
    def __init__(self, db_path: str):
        self.db_path = db_path
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def create_conversation(self, system_prompt: str, title: str = "Nova conversa") -> dict[str, Any]:
        now = time.time()
        conv = {"id": uuid.uuid4().hex, "title": title, "created_at": now, "updated_at": now}
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO conversations (id, title, system_prompt, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (conv["id"], title, system_prompt, now, now),
            )
        return conv

    def list_conversations(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, title, created_at, updated_at FROM conversations ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, title, system_prompt, created_at, updated_at FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
        return dict(row) if row else None

    def delete_conversation(self, conversation_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        return cur.rowcount > 0

    def get_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        """Histórico no formato da API: [{"role": ..., "content": ...}, ...]."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id",
                (conversation_id,),
            ).fetchall()
        return [{"role": r["role"], "content": json.loads(r["content"])} for r in rows]

    def append_exchange(self, conversation_id: str, user_content: Any, assistant_content: Any) -> None:
        """Salva a pergunta e a resposta juntas, para o histórico nunca ficar pela metade."""
        now = time.time()
        with self._connect() as conn:
            conn.executemany(
                "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                [
                    (conversation_id, "user", json.dumps(user_content), now),
                    (conversation_id, "assistant", json.dumps(assistant_content), now),
                ],
            )
            conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))

    def set_title(self, conversation_id: str, title: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE conversations SET title = ? WHERE id = ?", (title, conversation_id))
