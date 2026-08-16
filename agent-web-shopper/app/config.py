"""
Configurações centrais da aplicação.
Tudo que é "ajustável" mora aqui — nada de valores mágicos espalhados pelo código.
"""
from __future__ import annotations

import os
from pathlib import Path
from dataclasses import dataclass, field

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)


@dataclass(frozen=True)
class Settings:
    # --- LLM ---
    # "anthropic" (padrão, documentado no README) ou "gemini" (opcional, para
    # testar de graça sem gastar créditos do Claude — ver .env.example).
    llm_provider: str = os.getenv("LLM_PROVIDER", "anthropic")
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    llm_model: str = os.getenv("LLM_MODEL", "claude-sonnet-4-6")
    # Modelo mais barato por padrão: o selector-agent só gera JSON de seletores
    # a partir de HTML — tarefa estruturada onde um modelo menor já é suficiente,
    # e o gate de confiança em extract.py cobre o caso de seletor ruim.
    llm_model_selector: str = os.getenv("LLM_MODEL_SELECTOR", "claude-haiku-4-5")
    max_tokens: int = int(os.getenv("LLM_MAX_TOKENS", "2000"))
    # Quantas mensagens manter em _conversations entre turnos da mesma conversa
    # (agent.py) — evita que o histórico cresça sem limite.
    history_max_messages: int = int(os.getenv("HISTORY_MAX_MESSAGES", "20"))

    # --- Observabilidade / persistência ---
    db_path: str = os.getenv("DB_PATH", str(DATA_DIR / "traces.sqlite3"))
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    # --- Browser automation ---
    headless: bool = os.getenv("HEADLESS", "true").lower() == "true"
    nav_timeout_ms: int = int(os.getenv("NAV_TIMEOUT_MS", "20000"))
    max_sites_per_search: int = int(os.getenv("MAX_SITES_PER_SEARCH", "5"))

    # --- Web server ---
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8000"))


settings = Settings()
