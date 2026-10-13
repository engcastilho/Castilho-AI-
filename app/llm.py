"""Integração com a API do Claude (SDK oficial da Anthropic), com streaming."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import anthropic

from .config import Settings

# Se os classificadores de segurança recusarem um pedido, a API refaz a
# requisição automaticamente no modelo recomendado para aquela categoria.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass
class TextDelta:
    text: str


@dataclass
class Completed:
    content: list[dict[str, Any]]  # blocos da resposta, prontos para voltar ao histórico
    stop_reason: str | None


def history_content(content: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prepara os blocos de uma resposta para serem reenviados no histórico.

    Depois de um fallback no meio da resposta, apenas os blocos de texto que vêm
    antes do último bloco `fallback` podem ser reenviados; tudo depois dele volta
    normalmente.
    """
    last_fallback = max((i for i, b in enumerate(content) if b.get("type") == "fallback"), default=-1)
    if last_fallback < 0:
        return content
    before = [b for b in content[:last_fallback] if b.get("type") == "text"]
    return before + content[last_fallback + 1 :]


class ChatModel:
    def __init__(self, settings: Settings, client: anthropic.AsyncAnthropic | None = None):
        self.settings = settings
        self._client = client

    @property
    def client(self) -> anthropic.AsyncAnthropic:
        # Criado sob demanda: o servidor sobe mesmo sem a chave configurada.
        if self._client is None:
            self._client = anthropic.AsyncAnthropic()
        return self._client

    async def stream(self, system: str, messages: list[dict[str, Any]]) -> AsyncIterator[TextDelta | Completed]:
        async with self.client.beta.messages.stream(
            model=self.settings.model,
            max_tokens=self.settings.max_tokens,
            system=system,
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": self.settings.effort},
            cache_control={"type": "ephemeral"},  # reaproveita o histórico entre turnos (mais barato)
            betas=[FALLBACK_BETA],
            fallbacks="default",
        ) as stream:
            async for text in stream.text_stream:
                yield TextDelta(text)
            final = await stream.get_final_message()

        yield Completed(content=[block.to_dict() for block in final.content], stop_reason=final.stop_reason)
