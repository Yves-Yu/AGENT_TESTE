"""
Núcleo de rastreabilidade/observabilidade do agente.

Uso típico:

    with RunContext(channel="web", user_id="u1", input_text="...", criteria={...}) as run:
        with run.span("node.search_candidates", kind="node", input_data={"query": q}) as sp:
            candidates = do_search(q)
            sp.set_output(candidates)

        with run.span("llm.decide", kind="llm", input_data=prompt) as sp:
            answer = call_llm(prompt)
            sp.set_output(answer)
            sp.add_tokens(in_tokens, out_tokens, cost)

    # ao sair do `with RunContext(...)`, a run é fechada automaticamente
    # (status=success, ou failure se uma exceção não tratada escapou)

Cada span vira uma linha na tabela `spans`, com parent_span_id apontando pro
span "pai" (via contextvar) — isso reconstrói a árvore de execução completa
de qualquer run, o que é exatamente o que auditoria/observabilidade exige.
"""
from __future__ import annotations

import contextvars
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from app import db

_current_run: contextvars.ContextVar[Optional["RunContext"]] = contextvars.ContextVar(
    "_current_run", default=None
)
_current_span_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "_current_span_id", default=None
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_current_run() -> Optional["RunContext"]:
    return _current_run.get()


@dataclass
class SpanHandle:
    span_id: str
    run_id: str
    name: str
    _t0: float = field(default_factory=time.perf_counter)
    _output: Any = None
    _tokens_in: int = 0
    _tokens_out: int = 0
    _cost_usd: float = 0.0

    def set_output(self, output: Any) -> None:
        self._output = output

    def add_tokens(self, tokens_in: int = 0, tokens_out: int = 0, cost_usd: float = 0.0) -> None:
        self._tokens_in += tokens_in
        self._tokens_out += tokens_out
        self._cost_usd += cost_usd
        run = get_current_run()
        if run is not None:
            run.add_tokens(tokens_in, tokens_out, cost_usd)


class RunContext:
    """Representa uma execução completa do agente (uma tarefa do usuário)."""

    def __init__(self, channel: str, user_id: str, input_text: str, criteria: Optional[dict] = None):
        self.run_id = str(uuid.uuid4())
        self.channel = channel
        self.user_id = user_id
        self.input_text = input_text
        self.criteria = criteria or {}
        self._t0 = time.perf_counter()
        self.tokens_in = 0
        self.tokens_out = 0
        self.cost_usd = 0.0
        self.sites_visited = 0
        self.final_answer: Optional[str] = None
        self._token_reset = None
        self._run_reset = None

    # -- lifecycle -----------------------------------------------------
    def __enter__(self) -> "RunContext":
        db.insert_run(self.run_id, self.channel, self.user_id, self.input_text,
                       self.criteria, _now_iso())
        self._run_reset = _current_run.set(self)
        self._token_reset = _current_span_id.set(None)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        duration_ms = int((time.perf_counter() - self._t0) * 1000)
        status = "failure" if exc else "success"
        db.finish_run(
            self.run_id, status, self.final_answer,
            str(exc) if exc else None, _now_iso(), duration_ms,
            self.tokens_in, self.tokens_out, self.cost_usd, self.sites_visited,
        )
        _current_run.reset(self._run_reset)
        _current_span_id.reset(self._token_reset)
        return False  # não engole a exceção

    # -- helpers ---------------------------------------------------------
    def add_tokens(self, tokens_in: int, tokens_out: int, cost_usd: float) -> None:
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out
        self.cost_usd += cost_usd

    def visited_site(self) -> None:
        self.sites_visited += 1

    def set_final_answer(self, answer: str) -> None:
        self.final_answer = answer

    @contextmanager
    def span(self, name: str, kind: str, input_data: Any = None) -> Iterator[SpanHandle]:
        parent_id = _current_span_id.get()
        span_id = str(uuid.uuid4())
        db.insert_span(span_id, self.run_id, parent_id, name, kind, input_data, _now_iso())
        handle = SpanHandle(span_id=span_id, run_id=self.run_id, name=name)
        token = _current_span_id.set(span_id)
        t0 = time.perf_counter()
        try:
            yield handle
            duration_ms = int((time.perf_counter() - t0) * 1000)
            db.finish_span(span_id, "success", handle._output, None, _now_iso(),
                            duration_ms, handle._tokens_in, handle._tokens_out, handle._cost_usd)
        except Exception as e:
            duration_ms = int((time.perf_counter() - t0) * 1000)
            db.finish_span(span_id, "failure", handle._output, str(e), _now_iso(),
                            duration_ms, handle._tokens_in, handle._tokens_out, handle._cost_usd)
            raise
        finally:
            _current_span_id.reset(token)
