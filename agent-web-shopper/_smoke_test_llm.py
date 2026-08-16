"""Smoke test temporario do adaptador de LLM configurado (LLM_PROVIDER no .env)."""
from dotenv import load_dotenv

load_dotenv()

from app.config import settings
from app.llm import call_claude

print(f"Provider: {settings.llm_provider}")
print(f"Model:    {settings.llm_model}")
print(f"Selector: {settings.llm_model_selector}")

print("\n--- Teste 1: texto simples, sem tools ---")
r1 = call_claude(
    messages=[{"role": "user", "content": "Responda apenas com a palavra: OK"}],
    system="Voce e um assistente de teste. Responda de forma extremamente breve.",
)
print("stop_reason:", r1.stop_reason)
print("texto:", r1.content[0].text)
print("tokens in/out:", r1.usage.input_tokens, r1.usage.output_tokens)

print("\n--- Teste 2: com tool (forca tool_use) ---")
tools = [{
    "name": "get_weather",
    "description": "Retorna o clima atual de uma cidade.",
    "input_schema": {
        "type": "object",
        "properties": {"city": {"type": "string", "description": "Nome da cidade"}},
        "required": ["city"],
    },
}]
r2 = call_claude(
    messages=[{"role": "user", "content": "Qual o clima em Sao Paulo? Use a ferramenta disponivel."}],
    system="Voce e um assistente de teste.",
    tools=tools,
)
print("stop_reason:", r2.stop_reason)
for block in r2.content:
    if block.type == "text":
        print("  [text]", block.text)
    elif block.type == "tool_use":
        print(f"  [tool_use] id={block.id} name={block.name} input={block.input}")

print("\n--- Teste 3: enviar tool_result de volta (fecha o loop) ---")
tool_use_block = next(b for b in r2.content if b.type == "tool_use")
messages = [
    {"role": "user", "content": "Qual o clima em Sao Paulo? Use a ferramenta disponivel."},
    {"role": "assistant", "content": [
        {"type": "tool_use", "id": tool_use_block.id, "name": tool_use_block.name, "input": tool_use_block.input}
    ]},
    {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tool_use_block.id, "content": '{"temp_c": 24, "condicao": "ensolarado"}'}
    ]},
]
r3 = call_claude(messages=messages, system="Voce e um assistente de teste.", tools=tools)
print("stop_reason:", r3.stop_reason)
print("texto final:", r3.content[0].text if r3.content and r3.content[0].type == "text" else "(sem texto)")

ok = r1.stop_reason == "end_turn" and r2.stop_reason == "tool_use" and r3.stop_reason == "end_turn"
print("\n>>> TUDO OK <<<" if ok else "\n>>> ALGO FICOU DIFERENTE DO ESPERADO, ver acima <<<")
