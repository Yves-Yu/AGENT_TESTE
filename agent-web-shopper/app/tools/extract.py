"""
Extração de dados estruturados de páginas web.

Fluxo por chamada:
  1. Abre a URL via Playwright (browser.py)
  2. Consulta o cache de seletores por domínio (db.py); se não houver hit,
     chama o selector_agent para gerar seletores CSS/XPath para os campos pedidos
  3. Aplica os seletores no HTML e retorna os valores extraídos
  4. Seletor cacheado que não extraiu nada é tratado como stale: o cache é
     apagado e os seletores são gerados de novo uma vez (auto-cura contra
     sites que mudaram de HTML)

Isso centraliza toda a inteligência de extração: o agente principal
só chama fetch_and_extract(url, fields) sem saber nada de seletores.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from app import db
from app.tools.browser import get_page_html
from app.tools.selector_agent import generate_selectors
from app.tracing import get_current_run

logger = logging.getLogger(__name__)


def _domain(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


def _fields_hash(fields: dict[str, str]) -> str:
    normalized = json.dumps(sorted(fields.items()), ensure_ascii=False)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _apply_css(html: str, selector: str, attr: Optional[str]) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    elements = soup.select(selector)
    if attr:
        return [str(el.get(attr, "")) for el in elements if el.get(attr)]
    return [el.get_text(strip=True) for el in elements if el.get_text(strip=True)]


def _apply_xpath(html: str, xpath: str, attr: Optional[str]) -> list[str]:
    try:
        from lxml import etree  # import local para isolar falhas

        tree = etree.fromstring(html.encode("utf-8", errors="replace"), etree.HTMLParser())
        nodes = tree.xpath(xpath)
        if attr:
            return [str(n.get(attr, "")) for n in nodes if hasattr(n, "get") and n.get(attr)]
        return [
            n.text_content().strip()
            for n in nodes
            if hasattr(n, "text_content") and n.text_content().strip()
        ]
    except Exception as exc:
        logger.debug("XPath '%s' falhou: %s", xpath, exc)
        return []


def _apply_selectors(
    html: str, url: str, fields: dict[str, str], selector_data: dict
) -> dict[str, Any]:
    selectors = selector_data.get("selectors", {})
    confidences = selector_data.get("confidence", {})
    result: dict[str, Any] = {"url": url}

    for field_name in fields:
        sel_info = selectors.get(field_name)
        conf = float(confidences.get(field_name, 0))

        if not sel_info or conf < 0.3:
            result[field_name] = None
            continue

        sel_type = sel_info.get("type", "css")
        selector = sel_info.get("selector", "")
        attr: Optional[str] = sel_info.get("attr") or None

        try:
            values = (
                _apply_css(html, selector, attr)
                if sel_type == "css"
                else _apply_xpath(html, selector, attr)
            )
            result[field_name] = values[0] if values else None
        except Exception as exc:
            logger.warning("Seletor falhou para %s em %s: %s", field_name, url, exc)
            result[field_name] = None

    return result


def fetch_and_extract(url: str, fields: dict[str, str]) -> dict[str, Any]:
    """
    Abre a URL, gera (ou reaproveita do cache) seletores inteligentes e
    extrai os campos pedidos.

    Parâmetros:
        url    — URL da página
        fields — {nome_campo: descrição_do_campo}

    Retorna dict com url + valores por campo (None se não encontrado) + error opcional.
    """
    run = get_current_run()
    if run:
        run.visited_site()

    # --- 1. Carrega HTML ---
    try:
        html = get_page_html(url)
    except Exception as exc:
        return {"url": url, "error": f"Falha ao carregar página: {exc}"}

    domain = _domain(url)
    fields_hash = _fields_hash(fields)

    # --- 2. Seletores: cache por domínio+campos, ou gera via selector-agent ---
    cached = db.get_cached_selectors(domain, fields_hash)
    try:
        selector_data = cached if cached is not None else generate_selectors(html, fields)
    except Exception as exc:
        return {"url": url, "error": f"Falha ao gerar seletores: {exc}"}

    # --- 3. Aplica seletores ---
    result = _apply_selectors(html, url, fields, selector_data)

    # --- 4. Auto-cura: seletor cacheado que não extraiu nada de nenhum campo
    #        indica HTML mudado no site — descarta o cache e gera de novo ---
    if cached is not None and all(result.get(f) is None for f in fields):
        db.delete_cached_selectors(domain, fields_hash)
        try:
            selector_data = generate_selectors(html, fields)
        except Exception as exc:
            return {"url": url, "error": f"Falha ao gerar seletores: {exc}"}
        result = _apply_selectors(html, url, fields, selector_data)
        cached = None

    if cached is None and selector_data.get("selectors"):
        db.set_cached_selectors(domain, fields_hash, selector_data)

    return result
