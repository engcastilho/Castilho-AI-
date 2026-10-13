import json

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.llm import Completed, TextDelta, history_content
from app.main import create_app
from app.storage import Storage


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
def make_client(tmp_path):
    def _make(model):
        settings = Settings(db_path=str(tmp_path / "chat.db"), system_prompt="Sistema de teste")
        app = create_app(settings=settings, storage=Storage(settings.db_path), model=model)
        return TestClient(app)

    return _make


def events(response):
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def test_conversation_flow_streams_and_persists_history(make_client):
    model = FakeModel()
    client = make_client(model)

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


def test_refusal_is_not_saved(make_client):
    client = make_client(FakeModel(stop_reason="refusal"))
    conv = client.post("/api/conversations").json()

    evs = events(client.post(f"/api/conversations/{conv['id']}/messages", json={"content": "x"}))

    assert evs[-1]["type"] == "error" and evs[-1]["code"] == "refusal"
    assert client.get(f"/api/conversations/{conv['id']}").json()["messages"] == []


def test_api_error_becomes_friendly_message(make_client):
    client = make_client(FakeModel(error=RuntimeError("boom")))
    conv = client.post("/api/conversations").json()

    evs = events(client.post(f"/api/conversations/{conv['id']}/messages", json={"content": "x"}))

    assert evs == [{"type": "error", "code": "api_error", "message": "Erro inesperado ao gerar a resposta."}]


def test_list_and_delete_conversations(make_client):
    client = make_client(FakeModel())
    a = client.post("/api/conversations").json()
    client.post("/api/conversations")

    assert len(client.get("/api/conversations").json()) == 2
    assert client.delete(f"/api/conversations/{a['id']}").status_code == 204
    assert client.get(f"/api/conversations/{a['id']}").status_code == 404
    assert client.post(f"/api/conversations/{a['id']}/messages", json={"content": "x"}).status_code == 404


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


def test_index_page_is_served(make_client):
    res = make_client(FakeModel()).get("/")
    assert res.status_code == 200
    assert "Castilho AI" in res.text
