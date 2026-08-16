"""
Modelos Pydantic compartilhados pela aplicação.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    user_id: str = "anonymous"
    conversation_id: Optional[str] = None


class ChatResponse(BaseModel):
    answer: str
    run_id: str
    tokens_in: int
    tokens_out: int
    cost_usd: float


class RunSummary(BaseModel):
    run_id: str
    channel: str
    user_id: Optional[str]
    input_text: Optional[str]
    status: str
    started_at: str
    finished_at: Optional[str]
    duration_ms: Optional[int]
    tokens_in: int
    tokens_out: int
    cost_usd: float
    sites_visited: int
    final_answer: Optional[str]
    error: Optional[str]


class SpanSummary(BaseModel):
    span_id: str
    run_id: str
    parent_span_id: Optional[str]
    name: str
    kind: str
    status: str
    started_at: str
    finished_at: Optional[str]
    duration_ms: Optional[int]
    tokens_in: int
    tokens_out: int
    cost_usd: float
    error: Optional[str]
    input_json: Optional[str]
    output_json: Optional[str]


class MetricsSummary(BaseModel):
    total_runs: int
    successes: int
    failures: int
    avg_duration_ms: Optional[float]
    avg_tokens: Optional[float]
    total_cost_usd: float
