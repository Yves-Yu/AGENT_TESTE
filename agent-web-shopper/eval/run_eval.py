"""
Avaliador de qualidade do agente.

Executa os casos de eval/cases.jsonl, verifica critérios mínimos de qualidade
e gera eval/report.md com resultados detalhados.

Uso (da raiz do projeto):
    python -m eval.run_eval

Requer ANTHROPIC_API_KEY no ambiente (via .env ou variável de ambiente).
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Garante que o módulo 'app' seja encontrado independente de onde o script é chamado
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from app.db import init_db
from app.agent import run_agent

CASES_FILE = Path(__file__).parent / "cases.jsonl"
REPORT_FILE = Path(__file__).parent / "report.md"


# ----------------------------------------------------------------- helpers --

def load_cases() -> list[dict]:
    cases = []
    with open(CASES_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def evaluate_answer(answer: str, case: dict) -> dict[str, bool]:
    """
    Checks mínimos de qualidade para a resposta:
      non_empty         — resposta substancial (> 100 chars)
      in_portuguese     — contém palavras comuns do português
      mentions_expected — menciona ao menos um dos campos esperados
      not_fallback      — não é a mensagem genérica de fallback
      has_source        — cita ao menos um site/URL (rastreabilidade)
    """
    checks: dict[str, bool] = {}

    checks["non_empty"] = len(answer.strip()) > 100

    pt_words = ["para", "que", "com", "por", "mais", "preço", "produto",
                "opção", "disponível", "recomend", "melhor", "custo"]
    checks["in_portuguese"] = any(w in answer.lower() for w in pt_words)

    expected = case.get("expected_fields", [])
    checks["mentions_expected"] = (
        any(f.lower() in answer.lower() for f in expected) if expected else True
    )

    checks["not_fallback"] = "não consegui completar" not in answer.lower()

    # cita URL ou nome de loja conhecida
    source_hints = ["http", "www.", "mercadolivre", "amazon", "americanas",
                    "magazineluiza", "kabum", "shopee", "casasbahia"]
    checks["has_source"] = any(h in answer.lower() for h in source_hints)

    return checks


# ------------------------------------------------------------------ main ---

def run_evaluation() -> None:
    init_db()
    cases = load_cases()
    print(f"\n{'='*60}")
    print(f"  Shopping Agent — Avaliação ({len(cases)} casos)")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"{'='*60}\n")

    results = []
    for i, case in enumerate(cases, 1):
        print(f"[{i}/{len(cases)}] {case['input'][:65]}...")
        t0 = time.perf_counter()
        error = None
        result = None
        checks: dict[str, bool] = {}

        try:
            result = run_agent(case["input"], channel="eval", user_id=f"eval-{case['id']}")
            elapsed = time.perf_counter() - t0
            checks = evaluate_answer(result["answer"], case)
            success = sum(checks.values()) >= (len(checks) - 1)  # tolera 1 check falhando
            status = "✓ SUCESSO" if success else "✗ FALHA"
            print(
                f"  {status} | {elapsed:.1f}s | "
                f"tokens {result['tokens_in']}+{result['tokens_out']} | "
                f"${'%.4f' % result['cost_usd']}"
            )
        except Exception as exc:
            elapsed = time.perf_counter() - t0
            error = str(exc)
            success = False
            print(f"  ✗ EXCEÇÃO: {error}")

        results.append({
            "case": case,
            "result": result,
            "checks": checks,
            "success": success,
            "elapsed": elapsed,
            "error": error,
        })

    # ---------------------------------------------------------------- sumário
    successes = sum(1 for r in results if r["success"])
    total = len(results)
    success_rate = successes / total if total else 0
    valid_results = [r for r in results if r["result"]]
    avg_tokens = (
        sum(r["result"]["tokens_in"] + r["result"]["tokens_out"] for r in valid_results)
        / len(valid_results)
        if valid_results else 0
    )
    total_cost = sum(r["result"]["cost_usd"] for r in valid_results)
    avg_elapsed = sum(r["elapsed"] for r in results) / total if total else 0

    print(f"\n{'='*60}")
    print(f"  Casos executados : {total}")
    print(f"  Taxa de sucesso  : {successes}/{total} ({success_rate:.0%})")
    print(f"  Tokens médios    : {avg_tokens:.0f} tokens/execução")
    print(f"  Custo total      : ${total_cost:.4f} USD")
    print(f"  Tempo médio      : {avg_elapsed:.1f}s/execução")
    print(f"{'='*60}\n")

    write_report(results, successes, success_rate, avg_tokens, total_cost, avg_elapsed)
    print(f"Relatório salvo em: {REPORT_FILE}\n")


def write_report(
    results: list[dict],
    successes: int,
    success_rate: float,
    avg_tokens: float,
    total_cost: float,
    avg_elapsed: float,
) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        "# Relatório de Avaliação — Shopping Agent",
        "",
        f"**Data:** {ts}",
        "",
        "## Resumo",
        "",
        "| Métrica | Valor |",
        "|---|---|",
        f"| Casos executados | {len(results)} |",
        f"| Taxa de sucesso | {successes}/{len(results)} ({success_rate:.0%}) |",
        f"| Tokens médios por execução | {avg_tokens:.0f} |",
        f"| Custo total (USD) | ${total_cost:.4f} |",
        f"| Tempo médio por execução | {avg_elapsed:.1f}s |",
        "",
        "## Critérios de Avaliação",
        "",
        "Cada caso é avaliado em 5 checks. Um caso é marcado como SUCESSO se",
        "passar em pelo menos 4 dos 5 checks:",
        "",
        "| Check | Descrição |",
        "|---|---|",
        "| `non_empty` | Resposta tem mais de 100 caracteres |",
        "| `in_portuguese` | Contém palavras comuns do português |",
        "| `mentions_expected` | Menciona ao menos um dos campos esperados |",
        "| `not_fallback` | Não é a mensagem genérica de erro |",
        "| `has_source` | Cita ao menos um site ou URL (rastreabilidade) |",
        "",
        "## Casos Detalhados",
        "",
    ]

    for r in results:
        case = r["case"]
        status_str = "✓ SUCESSO" if r["success"] else "✗ FALHA"
        lines += [
            f"### Caso `{case['id']}` — {status_str}",
            "",
            f"**Descrição:** {case.get('description', '')}",
            "",
            f"**Input:** {case['input']}",
            "",
        ]

        if r["error"]:
            lines += [f"**Erro:** `{r['error']}`", ""]
        elif r["result"]:
            res = r["result"]
            chk = r["checks"]
            check_str = " | ".join(
                f"{'✓' if v else '✗'} {k}" for k, v in chk.items()
            )
            lines += [
                f"**Tokens:** {res['tokens_in']} entrada / {res['tokens_out']} saída",
                "",
                f"**Custo:** ${res['cost_usd']:.4f} USD",
                "",
                f"**Tempo:** {r['elapsed']:.1f}s",
                "",
                f"**Checks:** {check_str}",
                "",
                "**Resposta (trecho):**",
                "",
                "```",
                res["answer"][:400] + ("…" if len(res["answer"]) > 400 else ""),
                "```",
                "",
            ]

    REPORT_FILE.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    run_evaluation()
