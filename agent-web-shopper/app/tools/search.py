"""
Busca web usando DuckDuckGo (sem necessidade de API key).
"""
from __future__ import annotations

import logging

from duckduckgo_search import DDGS

from app.config import settings

logger = logging.getLogger(__name__)


def search_web(query: str, max_results: int | None = None) -> list[dict[str, str]]:
    """
    Pesquisa na web e retorna lista de resultados com url, title e snippet.
    Retorna lista vazia (sem exceção) se a busca falhar — o agente decide o que fazer.
    """
    max_results = max_results or settings.max_sites_per_search
    results: list[dict[str, str]] = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({
                    "url": r.get("href", ""),
                    "title": r.get("title", ""),
                    "snippet": r.get("body", ""),
                })
    except Exception as exc:
        logger.warning("Busca falhou para '%s': %s", query, exc)
    return results
