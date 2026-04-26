"""Tests du parser Confédération paysanne (communiqués de presse).

Tous offline : on injecte du HTML inline représentatif du rendu observé
sur https://www.confederationpaysanne.fr/recherche.php?type=RP au 2026-04-26
(20 entrées par page, dates DD.MM.YYYY, lien rp_article.php?id=N, titre dans
<div class="titre">, catégorie dans <div class="categorie">).
"""
from __future__ import annotations

from datetime import datetime

from src.sources import confederation_paysanne as mod


_SRC = {
    "id": "org_confederation_paysanne",
    "category": "communiques",
    "chamber": "ConfPaysanne",
    "url": "https://www.confederationpaysanne.fr/recherche.php?type=RP&raz=1&rech=0",
    "title_prefix": "Conf. paysanne —",
}


def _row(*, date: str, cat_id: int, cat_name: str, art_id: int, title: str) -> str:
    """Fabrique un bloc HTML reproduisant le rendu observé (un communiqué)."""
    return (
        '<div class="d-texte"><div class="bloc_mc_titre">'
        f'<a href="mc_nos_positions.php?mc={cat_id}">'
        f'<div class="categorie">{cat_name}</div></a>'
        f'<div class="f-date">{date}</div>'
        '</div></div>'
        f'<br><a href="rp_article.php?id={art_id}">'
        f'<div class="titre">{title}</div></a>'
    )


def _wrap(*rows: str) -> str:
    return f"<html><body>{''.join(rows)}</body></html>"


def test_parse_listing_basic():
    html = _wrap(
        _row(date="22.04.2026", cat_id=31, cat_name="PAC", art_id=16584,
             title="PAC : nouvelles exigences"),
        _row(date="21.04.2026", cat_id=26, cat_name="OGM", art_id=16579,
             title="La France vote pour la déréglementation des OGM"),
    )
    items = mod._parse_listing(html, _SRC)
    assert len(items) == 2

    a, b = items
    assert a.uid == "16584"
    assert a.title == "Conf. paysanne — PAC : nouvelles exigences"
    assert a.published_at == datetime(2026, 4, 22)
    assert a.url == "https://www.confederationpaysanne.fr/rp_article.php?id=16584"
    assert a.category == "communiques"
    assert a.chamber == "ConfPaysanne"
    assert a.raw["categorie"] == "PAC"

    assert b.uid == "16579"
    assert b.published_at == datetime(2026, 4, 21)
    assert b.raw["categorie"] == "OGM"


def test_parse_listing_empty_returns_empty():
    items = mod._parse_listing("<html><body><p>nothing</p></body></html>", _SRC)
    assert items == []


def test_parse_listing_skips_short_titles():
    """Un <a rp_article.php> sans contenu lisible est ignoré."""
    html = (
        '<html><body>'
        '<div class="f-date">10.04.2026</div>'
        '<a href="rp_article.php?id=999"><div class="titre">ok</div></a>'
        '<a href="rp_article.php?id=1000">'
        '<div class="titre">Titre suffisamment long</div></a>'
        '</body></html>'
    )
    items = mod._parse_listing(html, _SRC)
    assert len(items) == 1
    assert items[0].uid == "1000"


def test_parse_listing_invalid_date_yields_none():
    """Si la f-date n'est pas au format attendu, published_at = None."""
    html = (
        '<html><body>'
        '<div class="f-date">date inconnue</div>'
        '<a href="rp_article.php?id=42">'
        '<div class="titre">Communiqué sans date</div></a>'
        '</body></html>'
    )
    items = mod._parse_listing(html, _SRC)
    assert len(items) == 1
    assert items[0].published_at is None


def test_parse_listing_no_title_prefix():
    """`title_prefix` absent → titre brut."""
    src = dict(_SRC)
    src.pop("title_prefix")
    html = _wrap(
        _row(date="01.04.2026", cat_id=1, cat_name="Lait", art_id=100,
             title="Crise laitière"),
    )
    items = mod._parse_listing(html, src)
    assert items[0].title == "Crise laitière"


def test_with_dc_old_appends_query():
    """La pagination ajoute &dc_old=N en préservant les params existants."""
    base = "https://www.confederationpaysanne.fr/recherche.php?type=RP&raz=1&rech=0"
    assert mod._with_dc_old(base, 20).endswith("&dc_old=20")
    assert mod._with_dc_old("https://x.example/", 40) == "https://x.example/?dc_old=40"


def test_fetch_source_dedup_and_cutoff(monkeypatch):
    """Multi-pages : déduplication par uid + filtre cutoff_days appliqué."""
    page1 = _wrap(
        _row(date="22.04.2026", cat_id=1, cat_name="A", art_id=1, title="récent A"),
        _row(date="21.04.2026", cat_id=1, cat_name="A", art_id=2, title="récent B"),
    )
    page2 = _wrap(
        _row(date="20.04.2026", cat_id=1, cat_name="A", art_id=2,
             title="duplicat — doit être éliminé"),
        _row(date="01.01.2024", cat_id=1, cat_name="A", art_id=3,
             title="trop vieux — doit être filtré"),
    )

    calls: list[str] = []

    def fake_fetch_text(url, impersonate=False):
        calls.append(url)
        return page1 if "dc_old" not in url else page2

    monkeypatch.setattr(mod, "fetch_text", fake_fetch_text)

    src = dict(_SRC, pages=2, cutoff_days=180)
    items = mod.fetch_source(src)

    # 2 items conservés (id=1 et id=2). id=2 dupliqué de page2 ignoré.
    # id=3 (jan 2024) hors cutoff 180j depuis aujourd'hui → filtré.
    # Comme page2 ne produit aucun nouvel item après cutoff, la boucle s'arrête.
    assert {it.uid for it in items} == {"1", "2"}
    assert len(calls) == 2
