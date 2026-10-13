import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.auth import hash_password, verify_password
from app.config import Settings
from app.llm import Completed, TextDelta, history_content
from app.main import create_app
from app.storage import Storage

PASSWORD = "senha-segura-123"


class FakeModel:
    """Substitui a API do Claude nos testes."""

    def __init__(self, chunks=("Olá", ", mundo!"), stop_reason="end_turn", content=None, error=None):
        self.chunks = chunks
        self.stop_reason = stop_reason
        self.content = content
        self.error = error
        self.calls = []

    async def stream(self, system, messages):
        self.calls.append({"system": system, "messages": messages})
        if self.error:
            raise self.error
        for c in self.chunks:
            yield TextDelta(c)
        content = self.content or [
            {"type": "thinking", "thinking": "", "signature": "sig"},
            {"type": "text", "text": "".join(self.chunks)},
        ]
        yield Completed(content=content, stop_reason=self.stop_reason)


@pytest.fixture
def make_app(tmp_path):
    """Cria o app com um banco novo. Devolve uma função que gera navegadores (TestClient)."""

    def _make(model=None, **overrides):
        settings = Settings(db_path=str(tmp_path / "chat.db"), system_prompt="Sistema de teste", **overrides)
        app = create_app(settings=settings, storage=Storage(settings.db_path), model=model or FakeModel())
        return lambda: TestClient(app)

    return _make


def register(client, username, password=PASSWORD):
    return client.post("/api/auth/register", json={"username": username, "password": password})


def login(client, username, password=PASSWORD):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def events(response):
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def admin_client(make_app, model=None, **overrides):
    new_client = make_app(model, **overrides)
    admin = new_client()
    assert register(admin, "admin").json() == {"status": "ok"}
    return admin, new_client


# ---------- Senhas ----------


def test_password_hash_roundtrip():
    stored = hash_password(PASSWORD)
    assert PASSWORD not in stored
    assert verify_password(PASSWORD, stored)
    assert not verify_password("outra-senha", stored)
    assert not verify_password(PASSWORD, "lixo")


# ---------- Login ----------


def test_pages_require_login(make_app):
    client = make_app()()
    res = client.get("/", follow_redirects=False)
    assert res.status_code == 303 and res.headers["location"] == "/login"
    assert client.get("/login").status_code == 200
    assert client.get("/api/conversations").status_code == 401


def test_first_user_is_admin_and_others_wait_for_approval(make_app):
    admin, new_client = admin_client(make_app)
    assert admin.get("/api/auth/me").json()["is_admin"] is True

    maria = new_client()
    assert register(maria, "maria").json()["status"] == "pending"
    assert login(maria, "maria").status_code == 403

    users = admin.get("/api/admin/users").json()
    maria_id = next(u["id"] for u in users if u["username"] == "maria")
    assert admin.post(f"/api/admin/users/{maria_id}/approve").status_code == 200

    assert login(maria, "maria").status_code == 200
    me = maria.get("/api/auth/me").json()
    assert me == {"username": "maria", "is_admin": False, "daily_limit": 50, "used_today": 0}
    assert maria.get("/api/admin/users").status_code == 403

    # Bloquear derruba a sessão na hora.
    admin.post(f"/api/admin/users/{maria_id}/block")
    assert maria.get("/api/auth/me").status_code == 401


def test_registration_modes(make_app):
    _, new_client = admin_client(make_app, registration="open")
    assert register(new_client(), "joao").json() == {"status": "ok"}


def test_registration_closed(make_app):
    _, new_client = admin_client(make_app, registration="closed")
    assert register(new_client(), "joao").status_code == 403


def test_register_validation_and_duplicates(make_app):
    admin, new_client = admin_client(make_app)
    assert register(new_client(), "ab").status_code == 400
    assert register(new_client(), "joao", "curta").status_code == 400
    assert register(new_client(), "ADMIN").status_code == 409  # sem diferenciar maiúsculas


def test_wrong_password_and_lockout(make_app):
    _, new_client = admin_client(make_app)
    client = new_client()
    for _ in range(5):
        assert login(client, "admin", "errada-123").status_code == 401
    assert login(client, "admin").status_code == 429  # bloqueado mesmo com a senha certa
    assert login(client, "nao-existe").status_code == 401


def test_logout(make_app):
    admin, _ = admin_client(make_app)
    assert admin.post("/api/auth/logout").status_code == 204
    assert admin.get("/api/auth/me").status_code == 401


def test_password_is_not_stored_in_plain_text(make_app, tmp_path):
    admin_client(make_app)
    stored = sqlite3.connect(tmp_path / "chat.db").execute("SELECT password_hash FROM users").fetchone()[0]
    assert stored.startswith("scrypt$") and PASSWORD not in stored


# ---------- Conversas ----------


def test_conversation_flow_streams_and_persists_history(make_app):
    model = FakeModel()
    client, _ = admin_client(make_app, model)

    conv = client.post("/api/conversations").json()
    res = client.post(f"/api/conversations/{conv['id']}/messages", json={"content": "Oi, tudo bem?"})
    evs = events(res)

    assert [e["text"] for e in evs if e["type"] == "delta"] == ["Olá", ", mundo!"]
    assert evs[-1] == {"type": "done", "stop_reason": "end_turn", "title": "Oi, tudo bem?"}

    data = client.get(f"/api/conversations/{conv['id']}").json()
    assert data["title"] == "Oi, tudo bem?"
    assert data["messages"] == [
        {"role": "user", "text": "Oi, tudo bem?"},
        {"role": "assistant", "text": "Olá, mundo!"},
    ]

    # Segundo turno: o histórico volta intacto (com o bloco de thinking) e o system é o congelado.
    client.post(f"/api/conversations/{conv['id']}/messages", json={"content": "E agora?"})
    second = model.calls[1]
    assert second["system"] == "Sistema de teste"
    assert second["messages"][1]["content"][0]["type"] == "thinking"
    assert second["messages"][-1] == {"role": "user", "content": "E agora?"}


def test_users_only_see_their_own_conversations(make_app):
    admin, new_client = admin_client(make_app, registration="open")
    conv = admin.post("/api/conversations").json()

    other = new_client()
    register(other, "joao")
    assert other.get("/api/conversations").json() == []
    assert other.get(f"/api/conversations/{conv['id']}").status_code == 404
    assert other.delete(f"/api/conversations/{conv['id']}").status_code == 404
    assert other.post(f"/api/conversations/{conv['id']}/messages", json={"content": "x"}).status_code == 404


def test_daily_message_limit(make_app):
    _, new_client = admin_client(make_app, registration="open", daily_message_limit=2)
    client = new_client()
    register(client, "joao")
    conv = client.post("/api/conversations").json()
    url = f"/api/conversations/{conv['id']}/messages"

    assert client.post(url, json={"content": "1"}).status_code == 200
    assert client.post(url, json={"content": "2"}).status_code == 200
    res = client.post(url, json={"content": "3"})
    assert res.status_code == 429 and "limite de 2 mensagens" in res.json()["detail"]
    assert client.get("/api/auth/me").json()["used_today"] == 2


def test_refusal_is_not_saved(make_app):
    client, _ = admin_client(make_app, FakeModel(stop_reason="refusal"))
    conv = client.post("/api/conversations").json()

    evs = events(client.post(f"/api/conversations/{conv['id']}/messages", json={"content": "x"}))

    assert evs[-1]["type"] == "error" and evs[-1]["code"] == "refusal"
    assert client.get(f"/api/conversations/{conv['id']}").json()["messages"] == []


def test_api_error_becomes_friendly_message(make_app):
    client, _ = admin_client(make_app, FakeModel(error=RuntimeError("boom")))
    conv = client.post("/api/conversations").json()

    evs = events(client.post(f"/api/conversations/{conv['id']}/messages", json={"content": "x"}))

    assert evs == [{"type": "error", "code": "api_error", "message": "Erro inesperado ao gerar a resposta."}]


def test_history_content_after_mid_stream_fallback():
    content = [
        {"type": "thinking", "thinking": "", "signature": "a"},
        {"type": "text", "text": "parcial "},
        {"type": "fallback", "from": {"model": "x"}, "to": {"model": "y"}},
        {"type": "thinking", "thinking": "", "signature": "b"},
        {"type": "text", "text": "resto"},
    ]
    assert history_content(content) == [
        {"type": "text", "text": "parcial "},
        {"type": "thinking", "thinking": "", "signature": "b"},
        {"type": "text", "text": "resto"},
    ]
    assert history_content(content[:2]) == content[:2]


def test_old_database_is_migrated(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE conversations (id TEXT PRIMARY KEY, title TEXT NOT NULL, system_prompt TEXT NOT NULL,"
        " created_at REAL NOT NULL, updated_at REAL NOT NULL)"
    )
    conn.commit()
    conn.close()

    Storage(str(db))
    cols = [r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(conversations)")]
    assert "user_id" in cols


def test_cli_reset_password_and_make_admin(make_app, tmp_path, monkeypatch, capsys):
    from app import cli

    _, new_client = admin_client(make_app, registration="open")
    register(new_client(), "joao")

    monkeypatch.setattr(cli, "settings", Settings(db_path=str(tmp_path / "chat.db")))
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": "nova-senha-456")
    cli.main(["reset-password", "admin"])
    cli.main(["make-admin", "joao"])
    cli.main(["list-users"])
    out = capsys.readouterr().out
    assert "Senha de 'admin' alterada." in out and "'joao' agora é administrador." in out

    client = new_client()
    assert login(client, "admin").status_code == 401
    assert login(client, "admin", "nova-senha-456").status_code == 200
