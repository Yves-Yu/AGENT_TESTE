"""
Servidor FastAPI — expõe:
  GET  /              → interface de chat
  GET  /dashboard     → painel de observabilidade
  POST /api/chat      → endpoint principal do agente
  GET  /api/runs      → lista de execuções recentes
  GET  /api/runs/{id} → detalhes de uma execução
  GET  /api/runs/{id}/spans → spans (árvore de execução) de uma run
  GET  /api/metrics   → métricas agregadas
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from app import db
from app.agent import run_agent
from app.schemas import ChatRequest, ChatResponse

logger = logging.getLogger(__name__)

app = FastAPI(title="Shopping Agent", version="1.0.0")

_STATIC = Path(__file__).parent / "static"


# ---------------------------------------------------------------- páginas ---

@app.get("/", response_class=HTMLResponse)
async def chat_page() -> HTMLResponse:
    return HTMLResponse((_STATIC / "index.html").read_text(encoding="utf-8"))


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page() -> HTMLResponse:
    return HTMLResponse((_STATIC / "dashboard.html").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ API ----

@app.post("/api/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest) -> ChatResponse:
    """
    Executa o agente de compras em thread separada e retorna a resposta.
    A thread separada garante que o event loop do FastAPI não seja bloqueado
    pelas chamadas síncronas ao Playwright e à API do Claude.
    """
    try:
        result = await asyncio.to_thread(
            run_agent, request.message, "web", request.user_id, request.conversation_id
        )
    except Exception as exc:
        logger.error("Erro no agente: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Ocorreu um erro ao processar sua solicitação. Tente novamente em instantes.",
        )

    return ChatResponse(**result)


@app.get("/api/runs")
async def list_runs(limit: int = 50) -> list[dict]:
    return db.fetch_recent_runs(limit=min(limit, 200))


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str) -> dict:
    run = db.fetch_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run não encontrada")
    return run


@app.get("/api/runs/{run_id}/spans")
async def get_spans(run_id: str) -> list[dict]:
    return db.fetch_spans_for_run(run_id)


@app.get("/api/metrics")
async def metrics() -> dict:
    return db.aggregate_metrics()


# ---------------------------------------------------------- startup --------

@app.on_event("startup")
async def startup_event() -> None:
    db.init_db()
    logger.info("Banco de dados inicializado.")
