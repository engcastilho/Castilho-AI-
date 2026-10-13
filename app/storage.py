"""Persistência em SQLite: usuários, sessões de login e conversas.

O histórico é guardado exatamente como foi enviado à API (incluindo os blocos de
"thinking" das respostas), porque os modelos atuais exigem que o histórico seja
apenas acrescentado, nunca reescrito: o prompt de sistema é congelado na criação
da conversa e as mensagens nunca são editadas.
"""

import hashlib
import json
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    is_approved INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
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

USER_FIELDS = "id, username, is_admin, is_approved, created_at"
USER_FIELDS_JOINED = "u.id, u.username, u.is_admin, u.is_approved, u.created_at"


def _hash_token(token: str) -> str:
    # O banco guarda só o hash do token: quem copiar o banco não consegue entrar como ninguém.
    return hashlib.sha256(token.encode()).hexdigest()


class Storage:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            self._migrate(conn)
            conn.executescript(SCHEMA)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Atualiza bancos criados pela primeira versão (sem login)."""
        cols = [r[1] for r in conn.execute("PRAGMA table_info(conversations)")]
        if cols and "user_id" not in cols:
            conn.execute("ALTER TABLE conversations ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE")

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

    # ---------- Usuários ----------

    def count_users(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def create_user(self, username: str, password_hash: str, is_admin: bool, is_approved: bool) -> dict[str, Any]:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO users (username, password_hash, is_admin, is_approved, created_at) VALUES (?, ?, ?, ?, ?)",
                (username, password_hash, int(is_admin), int(is_approved), time.time()),
            )
        return self.get_user(cur.lastrowid)

    def get_user(self, user_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(f"SELECT {USER_FIELDS} FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None

    def get_user_with_password(self, username: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {USER_FIELDS}, password_hash FROM users WHERE username = ?", (username,)
            ).fetchone()
        return dict(row) if row else None

    def list_users(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(f"SELECT {USER_FIELDS} FROM users ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]

    def set_user_approved(self, user_id: int, approved: bool) -> bool:
        with self._connect() as conn:
            cur = conn.execute("UPDATE users SET is_approved = ? WHERE id = ?", (int(approved), user_id))
            if not approved:
                conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        return cur.rowcount > 0

    def delete_user(self, user_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        return cur.rowcount > 0

    # ---------- Sessões de login ----------

    def create_session(self, user_id: int, ttl_seconds: float) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
            conn.execute(
                "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                (_hash_token(token), user_id, now + ttl_seconds),
            )
        return token

    def get_session_user(self, token: str) -> dict[str, Any] | None:
        """Usuário dono de uma sessão válida (e ainda aprovado), ou None."""
        with self._connect() as conn:
            row = conn.execute(
                f"""SELECT {USER_FIELDS_JOINED}
                    FROM sessions s JOIN users u ON u.id = s.user_id
                    WHERE s.token_hash = ? AND s.expires_at > ? AND u.is_approved = 1""",
                (_hash_token(token), time.time()),
            ).fetchone()
        return dict(row) if row else None

    def delete_session(self, token: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))

    # ---------- Conversas ----------

    def create_conversation(self, user_id: int, system_prompt: str, title: str = "Nova conversa") -> dict[str, Any]:
        now = time.time()
        conv = {"id": uuid.uuid4().hex, "title": title, "created_at": now, "updated_at": now}
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO conversations (id, user_id, title, system_prompt, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (conv["id"], user_id, title, system_prompt, now, now),
            )
        return conv

    def list_conversations(self, user_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, title, created_at, updated_at FROM conversations WHERE user_id = ? ORDER BY updated_at DESC",
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_conversation(self, conversation_id: str, user_id: int) -> dict[str, Any] | None:
        """Só devolve a conversa se ela pertencer ao usuário."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, title, system_prompt, created_at, updated_at FROM conversations WHERE id = ? AND user_id = ?",
                (conversation_id, user_id),
            ).fetchone()
        return dict(row) if row else None

    def delete_conversation(self, conversation_id: str, user_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM conversations WHERE id = ? AND user_id = ?", (conversation_id, user_id))
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

    def count_user_messages_since(self, user_id: int, since: float) -> int:
        """Quantas mensagens o usuário enviou desde `since` (para o limite diário)."""
        with self._connect() as conn:
            return conn.execute(
                """SELECT COUNT(*) FROM messages m JOIN conversations c ON c.id = m.conversation_id
                   WHERE c.user_id = ? AND m.role = 'user' AND m.created_at >= ?""",
                (user_id, since),
            ).fetchone()[0]
