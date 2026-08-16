"""
Adaptador Gemini — só é importado quando LLM_PROVIDER=gemini (ver app/llm.py).

Traduz o formato de mensagens/tools no estilo Claude (usado por agent.py e
selector_agent.py) para a API do Gemini, e devolve a resposta encapsulada em
objetos com os mesmos atributos que anthropic.types.Message expõe
(.content com blocks .type/.text/.id/.name/.input, .stop_reason, .usage).
Assim o restante do app não precisa saber qual provider está rodando.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Optional

from google import genai
from google.genai import types

from app.config import settings

_client: Optional[genai.Client] = None

# Modelos Gemini 3.x exigem que o thought_signature de cada function_call seja
# reenviado junto quando o histórico é replayado (senão: 400 INVALID_ARGUMENT
# "Function call is missing a thought_signature"). O formato Claude não tem
# onde carregar esse campo extra, então guardamos por tool_use_id aqui —
# válido durante o processo (suficiente: cada run_agent() roda em um processo
# só, sem persistir/retomar conversas entre processos).
_thought_signatures: dict[str, bytes] = {}


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


class _TextBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _ToolUseBlock:
    type = "tool_use"

    def __init__(self, id: str, name: str, input: dict) -> None:
        self.id = id
        self.name = name
        self.input = input


class _Usage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class GeminiResponse:
    def __init__(self, content: list, stop_reason: str, usage: _Usage) -> None:
        self.content = content
        self.stop_reason = stop_reason
        self.usage = usage


def _sanitize_schema(schema: Any) -> Any:
    """O parameters do FunctionDeclaration do Gemini usa um subconjunto do
    JSON Schema (baseado no OpenAPI Schema) — não reconhece `additionalProperties`
    (usado no schema do fetch_and_extract para o campo `fields`, de chaves
    dinâmicas) e rejeita a requisição inteira com 400 se a chave aparecer.
    Remove recursivamente o que não é suportado; a description continua
    guiando o modelo sobre o formato esperado."""
    if not isinstance(schema, dict):
        return schema
    cleaned = {k: v for k, v in schema.items() if k != "additionalProperties"}
    if "properties" in cleaned:
        cleaned["properties"] = {k: _sanitize_schema(v) for k, v in cleaned["properties"].items()}
    if "items" in cleaned:
        cleaned["items"] = _sanitize_schema(cleaned["items"])
    return cleaned


def _to_gemini_tools(tools: Optional[list[dict]]) -> Optional[list[types.Tool]]:
    if not tools:
        return None
    declarations = [
        types.FunctionDeclaration(
            name=tool["name"],
            description=tool["description"],
            parameters=_sanitize_schema(tool["input_schema"]),
        )
        for tool in tools
    ]
    return [types.Tool(function_declarations=declarations)]


def _to_gemini_contents(messages: list[dict]) -> list[types.Content]:
    # tool_result no formato Claude só carrega tool_use_id — o Gemini casa a
    # resposta da função pelo nome, então guardamos esse mapeamento ao
    # percorrer os tool_use anteriores no histórico.
    id_to_name: dict[str, str] = {}
    contents: list[types.Content] = []

    for message in messages:
        role = message["role"]
        content = message["content"]
        gemini_role = "model" if role == "assistant" else "user"

        if isinstance(content, str):
            contents.append(types.Content(role=gemini_role, parts=[types.Part.from_text(text=content)]))
            continue

        parts: list[types.Part] = []
        for block in content:
            block_type = block.get("type")
            if block_type == "text":
                parts.append(types.Part.from_text(text=block["text"]))
            elif block_type == "tool_use":
                id_to_name[block["id"]] = block["name"]
                signature = _thought_signatures.get(block["id"])
                if signature is not None:
                    fc = types.FunctionCall(name=block["name"], args=block["input"])
                    parts.append(types.Part(function_call=fc, thought_signature=signature))
                else:
                    parts.append(types.Part.from_function_call(name=block["name"], args=block["input"]))
            elif block_type == "tool_result":
                name = id_to_name.get(block["tool_use_id"], "unknown_tool")
                raw = block.get("content", "")
                try:
                    payload = json.loads(raw) if isinstance(raw, str) else raw
                except json.JSONDecodeError:
                    payload = raw
                # FunctionResponse.response exige um dict — tool_result do
                # Claude aceita qualquer JSON (ex.: search_web devolve uma
                # lista), então embrulha o que não for dict.
                if not isinstance(payload, dict):
                    payload = {"result": payload}
                parts.append(types.Part.from_function_response(name=name, response=payload))

        contents.append(types.Content(role=gemini_role, parts=parts))

    return contents


def call_gemini(
    messages: list[dict],
    system: str,
    tools: Optional[list[dict]],
    model: str,
    max_tokens: int,
) -> GeminiResponse:
    config = types.GenerateContentConfig(
        system_instruction=system,
        max_output_tokens=max_tokens,
        tools=_to_gemini_tools(tools),
    )

    response = _get_client().models.generate_content(
        model=model,
        contents=_to_gemini_contents(messages),
        config=config,
    )

    candidate = response.candidates[0] if response.candidates else None
    parts = candidate.content.parts if candidate and candidate.content and candidate.content.parts else []

    text_chunks: list[str] = []
    tool_use_blocks: list[_ToolUseBlock] = []
    for part in parts:
        if part.text:
            text_chunks.append(part.text)
        elif part.function_call:
            call = part.function_call
            call_id = call.id or f"call_{uuid.uuid4().hex[:8]}"
            if part.thought_signature:
                _thought_signatures[call_id] = part.thought_signature
            tool_use_blocks.append(_ToolUseBlock(id=call_id, name=call.name, input=call.args or {}))

    content: list[Any] = []
    if text_chunks:
        content.append(_TextBlock("".join(text_chunks)))
    content.extend(tool_use_blocks)

    stop_reason = "tool_use" if tool_use_blocks else "end_turn"

    usage_meta = response.usage_metadata
    usage = _Usage(
        input_tokens=getattr(usage_meta, "prompt_token_count", 0) or 0,
        output_tokens=getattr(usage_meta, "candidates_token_count", 0) or 0,
    )

    return GeminiResponse(content=content, stop_reason=stop_reason, usage=usage)
