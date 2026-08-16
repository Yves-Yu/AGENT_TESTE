"""
Automação de browser com Playwright.
Navega até URLs e retorna o HTML da página para extração de dados.
"""
from __future__ import annotations

import logging

from playwright.sync_api import sync_playwright

from app.config import settings

logger = logging.getLogger(__name__)

_MAX_HTML_CHARS = 60_000
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def get_page_html(url: str) -> str:
    """
    Abre a URL com Playwright e retorna o HTML da página.
    Levanta exceção em caso de timeout ou erro de navegação.
    HTML é truncado em _MAX_HTML_CHARS para limitar o tamanho enviado ao LLM.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=settings.headless)
        try:
            ctx = browser.new_context(user_agent=_USER_AGENT)
            page = ctx.new_page()
            page.goto(
                url,
                timeout=settings.nav_timeout_ms,
                wait_until="domcontentloaded",
            )
            html = page.content()
        finally:
            browser.close()

    if len(html) > _MAX_HTML_CHARS:
        logger.debug("HTML truncado: %d → %d chars (%s)", len(html), _MAX_HTML_CHARS, url)
        html = html[:_MAX_HTML_CHARS]

    return html
