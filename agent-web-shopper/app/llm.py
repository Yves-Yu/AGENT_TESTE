"""
Wrapper centralizado para chamadas ao LLM.

Provider ativo controlado por LLM_PROVIDER (app/config.py):
  - "anthropic" (padrão — é o provider documentado no README e usado na
    avaliação do desafio)
  - "gemini"    (opcional — só para testar sem gastar créditos do Claude;
    ver .env.example e app/llm_gemini.py)

O nome da função (call_claude) foi mantido para não exigir nenhuma mudança em
agent.py / selector_agent.py: eles continuam chamando call_claude() e recebem
de volta um objeto com o mesmo formato (.content, .stop_reason, .usage),
independente de qual provider respondeu.

Centraliza contagem de tokens, cálculo de custo e integração com tracing.
"""
from __future__ import annotations

import logging
import random
import time
from typing import Any, Optional

import anthropic

from app.config import settings
from app.tracing import SpanHandle, get_current_run

logger = logging.getLogger(__name__)

# Preço por token (USD), por modelo — inclui cache write (escrita, ~1.25x o
# preço de input) e cache read (leitura, ~0.1x). Modelo desconhecido cai no
# preço do Sonnet (com warning) em vez de mascarar o custo real.
_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {
        "in": 3.0 / 1_000_000, "out": 15.0 / 1_000_000,
        "cache_write": 3.75 / 1_000_000, "cache_read": 0.30 / 1_000_000,
    },
    "claude-haiku-4-5": {
        "in": 1.0 / 1_000_000, "out": 5.0 / 1_000_000,
        "cache_write": 1.25 / 1_000_000, "cache_read": 0.10 / 1_000_000,
    },
}
_DEFAULT_PRICING = _PRICING["claude-sonnet-4-6"]

# Erros retryable: rate limit, falha de conexão, timeout, erro 5xx do servidor.
# Erros 4xx (request inválido, auth) não entram aqui — não adianta tentar de novo.
_RETRYABLE_ERRORS = (
    anthropic.RateLimitError,
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
    anthropic.InternalServerError,
)

_anthropic_client: Optional[anthropic.Anthropic] = None


def _get_anthropic_client() -> anthropic.Anthropic:
    global _anthropic_client
    if _anthropic_client is None:
        # max_retries=0: o retry é feito explicitamente por _create_with_retry,
        # que loga cada tentativa (o retry silencioso da SDK não aparece no tracing).
        # timeout=60s: bem menor que o default de 600s — numa run interativa,
        # esperar 10min por uma chamada travada é pior que falhar e tentar de novo.
        _anthropic_client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key, max_retries=0, timeout=60.0,
        )
    return _anthropic_client


def _create_with_retry(kwargs: dict[str, Any], max_attempts: int = 3) -> Any:
    for attempt in range(1, max_attempts + 1):
        try:
            return _get_anthropic_client().messages.create(**kwargs)
        except _RETRYABLE_ERRORS as exc:
            if attempt == max_attempts:
                raise
            delay = min(2 ** attempt, 10) + random.uniform(0, 0.5)
            logger.warning(
                "Chamada ao Claude falhou (tentativa %d/%d): %s — retry em %.1fs",
                attempt, max_attempts, exc, delay,
            )
            time.sleep(delay)


def call_claude(
    messages: list[dict],
    system: str,
    tools: Optional[list[dict]] = None,
    span_handle: Optional[SpanHandle] = None,
    model: Optional[str] = None,
    max_tokens: Optional[int] = None,
) -> Any:
    """
    Chama o LLM configurado e registra tokens + custo no span/run ativo.

    Se span_handle for passado, os tokens são adicionados a ele.
    Caso contrário, são adicionados ao RunContext ativo no contextvars.
    """
    model = model or settings.llm_model
    max_tokens = max_tokens or settings.max_tokens

    if settings.llm_provider == "gemini":
        from app.llm_gemini import call_gemini  # import tardio: só exige google-genai se este provider for usado

        response = call_gemini(messages, system, tools=tools, model=model, max_tokens=max_tokens)
        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens
        cost_usd = 0.0  # free tier do Gemini — sem custo a registrar
    else:
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            # Bloco com cache_control em vez de string simples: cacheia system
            # (e as tools, que vêm antes na ordem do request) entre chamadas
            # repetidas. Abaixo do mínimo de tokens cacheáveis do modelo isso é
            # um no-op silencioso — sem erro, sem custo extra.
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        response = _create_with_retry(kwargs)
        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens
        cache_write = getattr(response.usage, "cache_creation_input_tokens", 0) or 0
        cache_read = getattr(response.usage, "cache_read_input_tokens", 0) or 0

        pricing = _PRICING.get(model)
        if pricing is None:
            logger.warning("Preço desconhecido para o modelo %s — usando preço do Sonnet como fallback.", model)
            pricing = _DEFAULT_PRICING
        cost_usd = (
            tokens_in * pricing["in"]
            + tokens_out * pricing["out"]
            + cache_write * pricing["cache_write"]
            + cache_read * pricing["cache_read"]
        )

    if span_handle is not None:
        span_handle.add_tokens(tokens_in, tokens_out, cost_usd)
    else:
        run = get_current_run()
        if run is not None:
            run.add_tokens(tokens_in, tokens_out, cost_usd)

    logger.debug(
        "LLM call — provider=%s model=%s in=%d out=%d cost=$%.6f",
        settings.llm_provider, model, tokens_in, tokens_out, cost_usd,
    )
    return response
