"""Servidor web do chatbot: API REST + interface de chat em /."""

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import anthropic
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .auth import LoginLimiter, hash_password, validate_credentials, verify_password
from .config import Settings, settings as default_settings
from .llm import ChatModel, Completed, TextDelta, history_content
from .storage import Storage

STATIC_DIR = Path(__file__).parent / "static"
TITLE_MAX_CHARS = 60
SESSION_COOKIE = "castilho_session"
DAY_SECONDS = 24 * 60 * 60
# Usado quando o usuário não existe, para o login demorar o mesmo tempo e não
# revelar quais usuários estão cadastrados.
DUMMY_PASSWORD_HASH = hash_password("senha-qualquer")


class NewMessage(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)


class Credentials(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


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
    limiter = LoginLimiter()
    app = FastAPI(title="Castilho AI")

    # ---------- Quem está logado ----------

    def session_user(request: Request) -> dict[str, Any] | None:
        token = request.cookies.get(SESSION_COOKIE)
        return storage.get_session_user(token) if token else None

    def current_user(request: Request) -> dict[str, Any]:
        user = session_user(request)
        if user is None:
            raise HTTPException(401, "Faça login para continuar.")
        return user

    def admin_user(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        if not user["is_admin"]:
            raise HTTPException(403, "Apenas o administrador pode fazer isso.")
        return user

    def start_session(response: Response, user_id: int) -> None:
        max_age = settings.session_days * DAY_SECONDS
        token = storage.create_session(user_id, ttl_seconds=max_age)
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=max_age,
            httponly=True,  # o JavaScript da página não consegue ler o cookie
            samesite="lax",  # outros sites não conseguem usar o login do usuário
            secure=settings.cookie_secure,
        )

    def messages_last_24h(user: dict[str, Any]) -> int:
        return storage.count_user_messages_since(user["id"], time.time() - DAY_SECONDS)

    # ---------- Páginas ----------

    @app.get("/")
    async def index(request: Request):
        if session_user(request) is None:
            return RedirectResponse("/login", status_code=303)
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/login")
    async def login_page(request: Request):
        if session_user(request) is not None:
            return RedirectResponse("/", status_code=303)
        return FileResponse(STATIC_DIR / "login.html")

    @app.get("/admin")
    async def admin_page(request: Request):
        user = session_user(request)
        if user is None or not user["is_admin"]:
            return RedirectResponse("/", status_code=303)
        return FileResponse(STATIC_DIR / "admin.html")

    # ---------- Cadastro e login ----------

    @app.post("/api/auth/register", status_code=201)
    async def register(body: Credentials, response: Response):
        username = body.username.strip()
        error = validate_credentials(username, body.password)
        if error:
            raise HTTPException(400, error)

        first_user = storage.count_users() == 0
        if not first_user and settings.registration == "closed":
            raise HTTPException(403, "Os cadastros estão fechados no momento.")

        # A primeira conta criada vira administradora e já entra aprovada.
        approved = first_user or settings.registration == "open"
        try:
            user = storage.create_user(username, hash_password(body.password), is_admin=first_user, is_approved=approved)
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Esse nome de usuário já está em uso.") from None

        if first_user:
            storage.claim_orphan_conversations(user["id"])
        if not approved:
            return {"status": "pending", "message": "Conta criada! Aguarde o administrador aprovar seu acesso."}
        start_session(response, user["id"])
        return {"status": "ok"}

    @app.post("/api/auth/login")
    async def login(body: Credentials, response: Response):
        username = body.username.strip()
        if limiter.is_blocked(username):
            raise HTTPException(429, "Muitas tentativas erradas. Aguarde 15 minutos e tente de novo.")

        user = storage.get_user_with_password(username)
        password_ok = verify_password(body.password, user["password_hash"] if user else DUMMY_PASSWORD_HASH)
        if user is None or not password_ok:
            limiter.record_failure(username)
            raise HTTPException(401, "Usuário ou senha incorretos.")
        if not user["is_approved"]:
            raise HTTPException(403, "Sua conta ainda não foi aprovada pelo administrador.")

        limiter.reset(username)
        start_session(response, user["id"])
        return {"status": "ok"}

    @app.post("/api/auth/logout", status_code=204)
    async def logout(request: Request, response: Response):
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            storage.delete_session(token)
        response.delete_cookie(SESSION_COOKIE)

    @app.get("/api/auth/me")
    async def me(user: dict[str, Any] = Depends(current_user)):
        limit = 0 if user["is_admin"] else settings.daily_message_limit
        return {
            "username": user["username"],
            "is_admin": bool(user["is_admin"]),
            "daily_limit": limit,
            "used_today": messages_last_24h(user),
        }

    # ---------- Administração ----------

    @app.get("/api/admin/users")
    async def list_users(_: dict[str, Any] = Depends(admin_user)):
        return storage.list_users()

    @app.post("/api/admin/users/{user_id}/approve")
    async def approve_user(user_id: int, _: dict[str, Any] = Depends(admin_user)):
        if not storage.set_user_approved(user_id, True):
            raise HTTPException(404, "Usuário não encontrado")
        return {"status": "ok"}

    @app.post("/api/admin/users/{user_id}/block")
    async def block_user(user_id: int, admin: dict[str, Any] = Depends(admin_user)):
        if user_id == admin["id"]:
            raise HTTPException(400, "Você não pode bloquear a própria conta.")
        if not storage.set_user_approved(user_id, False):
            raise HTTPException(404, "Usuário não encontrado")
        return {"status": "ok"}

    @app.delete("/api/admin/users/{user_id}", status_code=204)
    async def delete_user(user_id: int, admin: dict[str, Any] = Depends(admin_user)):
        if user_id == admin["id"]:
            raise HTTPException(400, "Você não pode excluir a própria conta.")
        if not storage.delete_user(user_id):
            raise HTTPException(404, "Usuário não encontrado")

    # ---------- Conversas ----------

    @app.get("/api/conversations")
    async def list_conversations(user: dict[str, Any] = Depends(current_user)):
        return storage.list_conversations(user["id"])

    @app.post("/api/conversations", status_code=201)
    async def create_conversation(user: dict[str, Any] = Depends(current_user)):
        return storage.create_conversation(user["id"], system_prompt=settings.system_prompt)

    @app.get("/api/conversations/{conversation_id}")
    async def get_conversation(conversation_id: str, user: dict[str, Any] = Depends(current_user)):
        conv = storage.get_conversation(conversation_id, user["id"])
        if conv is None:
            raise HTTPException(404, "Conversa não encontrada")
        messages = [
            {"role": m["role"], "text": display_text(m["content"])} for m in storage.get_messages(conversation_id)
        ]
        return {"id": conv["id"], "title": conv["title"], "messages": messages}

    @app.delete("/api/conversations/{conversation_id}", status_code=204)
    async def delete_conversation(conversation_id: str, user: dict[str, Any] = Depends(current_user)):
        if not storage.delete_conversation(conversation_id, user["id"]):
            raise HTTPException(404, "Conversa não encontrada")

    @app.post("/api/conversations/{conversation_id}/messages")
    async def send_message(conversation_id: str, body: NewMessage, user: dict[str, Any] = Depends(current_user)):
        conv = storage.get_conversation(conversation_id, user["id"])
        if conv is None:
            raise HTTPException(404, "Conversa não encontrada")

        limit = settings.daily_message_limit
        if not user["is_admin"] and limit > 0 and messages_last_24h(user) >= limit:
            raise HTTPException(
                429, f"Você atingiu o limite de {limit} mensagens nas últimas 24 horas. Tente novamente mais tarde."
            )

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
