"""
Persistência de observabilidade.

Duas tabelas cobrem tudo que precisamos auditar:

  runs  -> uma execução completa do agente (uma pergunta/tarefa do usuário)
  spans -> cada etapa dentro de uma run (chamada de LLM, chamada de tool,
           nó do grafo), formando uma árvore via parent_span_id.

Com isso dá pra reconstruir, para qualquer run_id, exatamente o que o agente
fez, em que ordem, com quais inputs/outputs, quanto tempo levou e quantos
tokens consumiu em cada etapa — o requisito de rastreabilidade do desafio.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from app.config import settings

_local = threading.local()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id          TEXT PRIMARY KEY,
    channel         TEXT NOT NULL,          -- 'web' | 'eval'
    user_id         TEXT,
    input_text      TEXT,
    criteria_json   TEXT,
    status          TEXT NOT NULL DEFAULT 'running',  -- running|success|failure
    final_answer    TEXT,
    error           TEXT,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    duration_ms     INTEGER,
    tokens_in       INTEGER DEFAULT 0,
    tokens_out      INTEGER DEFAULT 0,
    cost_usd        REAL DEFAULT 0,
    sites_visited   INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS spans (
    span_id         TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL,
    parent_span_id  TEXT,
    name            TEXT NOT NULL,          -- ex: 'node.search_candidates', 'llm.decide', 'tool.extract'
    kind            TEXT NOT NULL,          -- 'node' | 'llm' | 'tool'
    input_json      TEXT,
    output_json     TEXT,
    status          TEXT NOT NULL DEFAULT 'running',
    error           TEXT,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    duration_ms     INTEGER,
    tokens_in       INTEGER DEFAULT 0,
    tokens_out      INTEGER DEFAULT 0,
    cost_usd        REAL DEFAULT 0,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_spans_run_id ON spans(run_id);

CREATE TABLE IF NOT EXISTS selector_cache (
    domain          TEXT NOT NULL,
    fields_hash     TEXT NOT NULL,
    selectors_json  TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (domain, fields_hash)
);
"""


def get_conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn"):
        conn = sqlite3.connect(settings.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")  # permite leitura concorrente com escrita
        _local.conn = conn
    return _local.conn


def init_db() -> None:
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()


@contextmanager
def cursor() -> Iterator[sqlite3.Cursor]:
    conn = get_conn()
    cur = conn.cursor()
    try:
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


# ---------------------------------------------------------------- runs ----

def insert_run(run_id: str, channel: str, user_id: str, input_text: str,
                criteria: dict, started_at: str) -> None:
    with cursor() as cur:
        cur.execute(
            "INSERT INTO runs (run_id, channel, user_id, input_text, criteria_json, started_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, channel, user_id, input_text, json.dumps(criteria, ensure_ascii=False), started_at),
        )


def finish_run(run_id: str, status: str, final_answer: Optional[str], error: Optional[str],
                finished_at: str, duration_ms: int, tokens_in: int, tokens_out: int,
                cost_usd: float, sites_visited: int) -> None:
    with cursor() as cur:
        cur.execute(
            "UPDATE runs SET status=?, final_answer=?, error=?, finished_at=?, duration_ms=?, "
            "tokens_in=?, tokens_out=?, cost_usd=?, sites_visited=? WHERE run_id=?",
            (status, final_answer, error, finished_at, duration_ms, tokens_in, tokens_out,
             cost_usd, sites_visited, run_id),
        )


def fetch_run(run_id: str) -> Optional[dict]:
    with cursor() as cur:
        cur.execute("SELECT * FROM runs WHERE run_id=?", (run_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def fetch_recent_runs(limit: int = 50) -> list[dict]:
    with cursor() as cur:
        cur.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,))
        return [dict(r) for r in cur.fetchall()]


def aggregate_metrics() -> dict:
    with cursor() as cur:
        cur.execute("""
            SELECT
                COUNT(*)                                       AS total_runs,
                SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS successes,
                SUM(CASE WHEN status='failure' THEN 1 ELSE 0 END) AS failures,
                AVG(duration_ms)                               AS avg_duration_ms,
                AVG(tokens_in + tokens_out)                    AS avg_tokens,
                SUM(cost_usd)                                  AS total_cost_usd
            FROM runs WHERE status != 'running'
        """)
        row = cur.fetchone()
        return dict(row) if row else {}


# --------------------------------------------------------------- spans ----

def insert_span(span_id: str, run_id: str, parent_span_id: Optional[str], name: str,
                 kind: str, input_json: Any, started_at: str) -> None:
    with cursor() as cur:
        cur.execute(
            "INSERT INTO spans (span_id, run_id, parent_span_id, name, kind, input_json, started_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (span_id, run_id, parent_span_id, name, kind,
             json.dumps(input_json, ensure_ascii=False, default=str), started_at),
        )


def finish_span(span_id: str, status: str, output_json: Any, error: Optional[str],
                 finished_at: str, duration_ms: int, tokens_in: int = 0,
                 tokens_out: int = 0, cost_usd: float = 0) -> None:
    with cursor() as cur:
        cur.execute(
            "UPDATE spans SET status=?, output_json=?, error=?, finished_at=?, duration_ms=?, "
            "tokens_in=?, tokens_out=?, cost_usd=? WHERE span_id=?",
            (status, json.dumps(output_json, ensure_ascii=False, default=str), error,
             finished_at, duration_ms, tokens_in, tokens_out, cost_usd, span_id),
        )


def fetch_spans_for_run(run_id: str) -> list[dict]:
    with cursor() as cur:
        cur.execute("SELECT * FROM spans WHERE run_id=? ORDER BY started_at ASC", (run_id,))
        return [dict(r) for r in cur.fetchall()]


# ------------------------------------------------------------ selector_cache ---

def get_cached_selectors(domain: str, fields_hash: str) -> Optional[dict]:
    with cursor() as cur:
        cur.execute(
            "SELECT selectors_json FROM selector_cache WHERE domain=? AND fields_hash=?",
            (domain, fields_hash),
        )
        row = cur.fetchone()
        return json.loads(row["selectors_json"]) if row else None


def set_cached_selectors(domain: str, fields_hash: str, selector_data: dict) -> None:
    with cursor() as cur:
        cur.execute(
            "INSERT OR REPLACE INTO selector_cache (domain, fields_hash, selectors_json, created_at) "
            "VALUES (?, ?, ?, ?)",
            (domain, fields_hash, json.dumps(selector_data, ensure_ascii=False), _now_iso()),
        )


def delete_cached_selectors(domain: str, fields_hash: str) -> None:
    with cursor() as cur:
        cur.execute(
            "DELETE FROM selector_cache WHERE domain=? AND fields_hash=?",
            (domain, fields_hash),
        )
