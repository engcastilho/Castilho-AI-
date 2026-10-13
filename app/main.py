"""Servidor web do chatbot: API REST + interface de chat em /."""

import json
from pathlib import Path
from typing import Any

import anthropic
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import Settings, settings as default_settings
from .llm import ChatModel, Completed, TextDelta, history_content
from .storage import Storage

STATIC_DIR = Path(__file__).parent / "static"
TITLE_MAX_CHARS = 60


class NewMessage(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)


def display_text(content: Any) -> str:
    """Texto visível de uma mensagem (ignora blocos de thinking, fallback etc.)."""
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if b.get("type") == "text")


def make_title(text: str) -> str:
    title = " ".join(text.split())
    return title if len(title) <= TITLE_MAX_CHARS else title[: TITLE_MAX_CHARS - 1].rstrip() + "…"


def friendly_error(exc: Exception) -> str:
    if isinstance(exc, anthropic.AuthenticationError):
        return "Chave da API inválida. Verifique ANTHROPIC_API_KEY no arquivo .env."
    if isinstance(exc, anthropic.RateLimitError):
        return "Limite de uso da API atingido. Aguarde alguns instantes e tente de novo."
    if isinstance(exc, anthropic.APIStatusError):
        return f"A API retornou um erro ({exc.status_code}). Tente novamente."
    if isinstance(exc, anthropic.APIConnectionError):
        return "Não foi possível conectar à API da Anthropic. Verifique sua conexão."
    if isinstance(exc, TypeError) and "Could not resolve authentication" in str(exc):
        # O SDK levanta TypeError quando não encontra nenhuma credencial.
        return "Credenciais não configuradas. Defina ANTHROPIC_API_KEY no arquivo .env."
    return "Erro inesperado ao gerar a resposta."


def event(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


def create_app(
    settings: Settings = default_settings,
    storage: Storage | None = None,
    model: ChatModel | None = None,
) -> FastAPI:
    storage = storage or Storage(settings.db_path)
    model = model or ChatModel(settings)
    app = FastAPI(title="Castilho AI")

    @app.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/conversations")
    async def list_conversations():
        return storage.list_conversations()

    @app.post("/api/conversations", status_code=201)
    async def create_conversation():
        return storage.create_conversation(system_prompt=settings.system_prompt)

    @app.get("/api/conversations/{conversation_id}")
    async def get_conversation(conversation_id: str):
        conv = storage.get_conversation(conversation_id)
        if conv is None:
            raise HTTPException(404, "Conversa não encontrada")
        messages = [
            {"role": m["role"], "text": display_text(m["content"])} for m in storage.get_messages(conversation_id)
        ]
        return {"id": conv["id"], "title": conv["title"], "messages": messages}

    @app.delete("/api/conversations/{conversation_id}", status_code=204)
    async def delete_conversation(conversation_id: str):
        if not storage.delete_conversation(conversation_id):
            raise HTTPException(404, "Conversa não encontrada")

    @app.post("/api/conversations/{conversation_id}/messages")
    async def send_message(conversation_id: str, body: NewMessage):
        conv = storage.get_conversation(conversation_id)
        if conv is None:
            raise HTTPException(404, "Conversa não encontrada")

        history = storage.get_messages(conversation_id)
        user_content = body.content
        messages = history + [{"role": "user", "content": user_content}]

        async def generate():
            try:
                async for item in model.stream(conv["system_prompt"], messages):
                    if isinstance(item, TextDelta):
                        yield event({"type": "delta", "text": item.text})
                    elif isinstance(item, Completed):
                        if item.stop_reason == "refusal":
                            # A resposta parcial de uma recusa é descartada e não entra no histórico.
                            yield event(
                                {
                                    "type": "error",
                                    "code": "refusal",
                                    "message": "Não posso ajudar com essa solicitação. Tente reformulá-la.",
                                }
                            )
                            return
                        storage.append_exchange(conversation_id, user_content, history_content(item.content))
                        title = None
                        if not history:
                            title = make_title(user_content)
                            storage.set_title(conversation_id, title)
                        yield event({"type": "done", "stop_reason": item.stop_reason, "title": title})
            except Exception as exc:  # noqa: BLE001 - o erro vira uma mensagem amigável no chat
                yield event({"type": "error", "code": "api_error", "message": friendly_error(exc)})

        return StreamingResponse(generate(), media_type="application/x-ndjson")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()
