"""
Agente principal de pesquisa de compras.

Implementa um loop agentico simples usando a API do Claude com tool use:
  1. Envia a mensagem do usuário com definição das ferramentas disponíveis
  2. Claude decide chamar ferramenta(s) → executa → devolve resultado
  3. Repete até Claude retornar stop_reason == "end_turn"

Cada chamada de LLM e cada chamada de ferramenta gera um span no RunContext
(tracing.py), garantindo rastreabilidade completa: entrada, saída, tempo e
tokens de cada etapa ficam persistidos no SQLite.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from app.config import settings
from app.llm import call_claude
from app.prompts import get_main_agent_prompt
from app.tools.extract import fetch_and_extract as _fetch_and_extract
from app.tools.search import search_web as _search_web
from app.tracing import RunContext

logger = logging.getLogger(__name__)

_MAX_ITERATIONS = 12  # teto para evitar loop infinito

# Memória mínima entre turnos, em processo (reseta se o servidor reiniciar).
# Chave = conversation_id (gerado por sessão de navegador) ou user_id.
_conversations: dict[str, list[dict]] = {}

TOOLS: list[dict] = [
    {
        "name": "search_web",
        "description": (
            "Pesquisa na web e retorna uma lista de sites/páginas relevantes. "
            "Use para descobrir lojas e páginas de produto para o item solicitado."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Termo de busca. Inclua nome do produto, contexto de compra e "
                        "país/idioma quando relevante. "
                        "Exemplo: 'comprar notebook Dell inspiron Brasil 2024'."
                    ),
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "fetch_and_extract",
        "description": (
            "Abre uma URL com navegador real e extrai os campos solicitados. "
            "Use para coletar dados de um produto em uma loja específica encontrada via search_web."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL completa da página do produto ou categoria.",
                },
                "fields": {
                    "type": "object",
                    "description": (
                        "Campos a extrair. Chave = nome do campo, valor = descrição detalhada "
                        "do que o campo deve conter. "
                        'Exemplo: {"price": "preço atual de venda sem parcelamento", '
                        '"name": "nome completo do produto conforme anunciado"}.'
                    ),
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["url", "fields"],
        },
    },
]


def _apply_cache_breakpoint(messages: list[dict]) -> None:
    """
    Marca cache_control no último bloco da última mensagem, e remove
    marcações deixadas por iterações anteriores — o request da Anthropic
    aceita no máximo 4 breakpoints por chamada, e sem essa limpeza uma run de
    até 12 iterações estouraria esse limite. Isso cacheia o prefixo crescente
    da conversa a cada iteração do loop (além do cache estático de
    system+tools já aplicado em app/llm.py).
    """
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                block.pop("cache_control", None)
    if not messages:
        return
    last = messages[-1]
    if isinstance(last["content"], str):
        last["content"] = [{"type": "text", "text": last["content"]}]
    if last["content"]:
        last["content"][-1]["cache_control"] = {"type": "ephemeral"}


def _execute_tool(name: str, tool_input: dict, run: RunContext) -> tuple[str, bool]:
    """Executa a ferramenta e retorna (resultado como string JSON, is_error)."""
    with run.span(f"tool.{name}", kind="tool", input_data=tool_input) as sp:
        try:
            if name == "search_web":
                result: Any = _search_web(tool_input["query"])
            elif name == "fetch_and_extract":
                result = _fetch_and_extract(
                    tool_input["url"], tool_input.get("fields", {})
                )
            else:
                result = {"error": f"Ferramenta desconhecida: {name}"}
            sp.set_output(result)
            is_error = isinstance(result, dict) and "error" in result
        except Exception as exc:
            err = str(exc)
            logger.warning("Ferramenta %s falhou: %s", name, err)
            sp.set_output({"error": err})
            result = {"error": err}
            is_error = True
        return json.dumps(result, ensure_ascii=False, default=str), is_error


def run_agent(
    input_text: str,
    channel: str = "web",
    user_id: str = "anonymous",
    conversation_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    Executa o agente de compras e retorna:
      - answer   : resposta final em texto
      - run_id   : UUID da execução (para auditoria no dashboard)
      - tokens_in / tokens_out : totais acumulados de tokens
      - cost_usd : custo estimado em USD

    conversation_id (se informado) identifica a conversa para efeito de
    memória entre turnos; se omitido, cai para user_id.
    """
    system_prompt = get_main_agent_prompt()
    memory_key = conversation_id or user_id
    history = _conversations.get(memory_key, [])

    with RunContext(channel=channel, user_id=user_id, input_text=input_text) as run:
        messages: list[dict] = history + [{"role": "user", "content": input_text}]

        for iteration in range(_MAX_ITERATIONS):
            _apply_cache_breakpoint(messages)
            with run.span(
                "llm.call",
                kind="llm",
                input_data={"iteration": iteration, "n_messages": len(messages)},
            ) as sp:
                response = call_claude(
                    messages,
                    system_prompt,
                    tools=TOOLS,
                    span_handle=sp,
                )
                sp.set_output({
                    "stop_reason": response.stop_reason,
                    "n_blocks": len(response.content),
                    "text": " ".join(
                        b.text for b in response.content if b.type == "text"
                    ).strip(),
                    "tool_calls": [
                        {"name": b.name, "input": b.input}
                        for b in response.content if b.type == "tool_use"
                    ],
                })

            # Reconstrói o turno do assistant para o histórico
            assistant_blocks: list[dict] = []
            for block in response.content:
                if block.type == "text":
                    assistant_blocks.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    assistant_blocks.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })
            messages.append({"role": "assistant", "content": assistant_blocks})

            if response.stop_reason == "end_turn":
                final_text = " ".join(
                    b.text for b in response.content if b.type == "text"
                ).strip()
                run.set_final_answer(final_text)
                _save_history(memory_key, messages)
                return _result(run, final_text)

            if response.stop_reason == "tool_use":
                tool_results: list[dict] = []
                for block in response.content:
                    if block.type == "tool_use":
                        outcome, is_error = _execute_tool(block.name, block.input, run)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": outcome,
                            "is_error": is_error,
                        })
                messages.append({"role": "user", "content": tool_results})
                continue

            logger.warning("stop_reason inesperado: %s", response.stop_reason)
            break

        fallback = (
            "Não consegui completar a pesquisa dentro do limite de iterações. "
            "Por favor, tente novamente com um pedido mais específico."
        )
        run.set_final_answer(fallback)
        _save_history(memory_key, messages)
        return _result(run, fallback)


def _save_history(memory_key: str, messages: list[dict]) -> None:
    """Salva o histórico da conversa truncado às últimas N mensagens
    (settings.history_max_messages) — evita que _conversations cresça sem
    limite entre turnos da mesma conversa."""
    _conversations[memory_key] = messages[-settings.history_max_messages:]


def _result(run: RunContext, answer: str) -> dict[str, Any]:
    return {
        "answer": answer,
        "run_id": run.run_id,
        "tokens_in": run.tokens_in,
        "tokens_out": run.tokens_out,
        "cost_usd": run.cost_usd,
    }
