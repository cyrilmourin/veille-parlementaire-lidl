"""Connecteur Confédération paysanne — communiqués de presse.

Le site n'expose pas de RSS. La liste des communiqués est servie par
`recherche.php?type=RP&raz=1&rech=0` (par paquets de 20, pagination via
`&dc_old=N`). HTML artisanal (PHP table-soup, classes en français), parsé
par appariement positionnel dans l'ordre du document : pour chaque
`<a href="rp_article.php?id=N">`, la `<div class="f-date">` rencontrée
juste avant donne la date du communiqué (format DD.MM.YYYY).

Les sélecteurs génériques de `html_generic` ne fonctionnent pas ici parce
que le site ne pose ni `<article>`, ni `<time>`, ni de date dans l'URL,
et que le pattern `href*="communique"` rate les liens `rp_article.php`.

Configuration YAML attendue (extrait) :

    - id: org_confederation_paysanne
      category: communiques
      url: https://www.confederationpaysanne.fr/recherche.php?type=RP&raz=1&rech=0
      format: confederation_paysanne_listing
      chamber: ConfPaysanne
      title_prefix: "Conf. paysanne —"
      pages: 2          # optionnel, défaut 2 (40 derniers communiqués)
      cutoff_days: 120  # optionnel, défaut 120
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import Item
from ._common import fetch_text

log = logging.getLogger(__name__)

_DATE_RE = re.compile(r"^\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\s*$")
_ID_RE = re.compile(r"id=(\d+)")
_DEFAULT_PAGES = 2
_DEFAULT_CUTOFF_DAYS = 120


def _parse_listing(html: str, src: dict) -> list[Item]:
    base_url = src["url"]
    title_prefix = src.get("title_prefix", "")
    soup = BeautifulSoup(html, "html.parser")
    out: list[Item] = []
    last_date: datetime | None = None
    last_categorie: str | None = None
    for el in soup.descendants:
        name = getattr(el, "name", None)
        if not name:
            continue
        cls = el.get("class") or []
        if "f-date" in cls:
            text = el.get_text(" ", strip=True) or ""
            m = _DATE_RE.match(text)
            if m:
                d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
                try:
                    last_date = datetime(y, mo, d)
                except ValueError:
                    last_date = None
            else:
                last_date = None
            continue
        if "categorie" in cls:
            last_categorie = el.get_text(" ", strip=True) or None
            continue
        if name == "a":
            href = el.get("href") or ""
            if "rp_article.php" not in href:
                continue
            full_url = urljoin(base_url, href)
            title_div = el.find(class_="titre")
            title = (title_div.get_text(" ", strip=True) if title_div
                     else el.get_text(" ", strip=True))
            title = (title or "").strip()
            if not title or len(title) < 5:
                continue
            m_id = _ID_RE.search(href)
            uid = m_id.group(1) if m_id else full_url
            display_title = (
                f"{title_prefix} {title}".strip() if title_prefix else title
            )[:240]
            summary = f"Catégorie : {last_categorie}" if last_categorie else ""
            out.append(Item(
                source_id=src["id"],
                uid=uid[:200],
                category=src["category"],
                chamber=src.get("chamber"),
                title=display_title,
                url=full_url,
                published_at=last_date,
                summary=summary[:2000],
                raw={
                    "path": "confpays_listing",
                    "id": uid,
                    "categorie": last_categorie or "",
                },
            ))
    return out


def _with_dc_old(url: str, offset: int) -> str:
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}dc_old={offset}"


def fetch_source(src: dict) -> list[Item]:
    base_url = src["url"]
    pages = max(1, int(src.get("pages", _DEFAULT_PAGES)))
    cutoff_days = int(src.get("cutoff_days", _DEFAULT_CUTOFF_DAYS))
    cutoff = datetime.utcnow() - timedelta(days=cutoff_days)
    impersonate = bool(src.get("impersonate", False))

    all_items: list[Item] = []
    seen: set[str] = set()
    for p in range(pages):
        page_url = base_url if p == 0 else _with_dc_old(base_url, p * 20)
        try:
            html = fetch_text(page_url, impersonate=impersonate)
        except Exception as e:
            log.warning("Conf. paysanne KO page %d (%s) : %s", p, page_url, e)
            break
        items = _parse_listing(html, src)
        if not items:
            log.info("Conf. paysanne : aucune entrée page %d — stop", p)
            break
        kept = 0
        for it in items:
            if it.uid in seen:
                continue
            seen.add(it.uid)
            if it.published_at and it.published_at < cutoff:
                continue
            all_items.append(it)
            kept += 1
        log.info(
            "Conf. paysanne : page %d → %d items (cutoff %dj)",
            p, kept, cutoff_days,
        )
        # Listing trié desc : si la page n'a rien apporté après cutoff,
        # les pages suivantes sont nécessairement hors fenêtre.
        if kept == 0:
            break
    log.info(
        "Conf. paysanne : %d items au total (max %d pages)",
        len(all_items), pages,
    )
    return all_items
