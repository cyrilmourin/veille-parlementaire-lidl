"""P4 (2026-04-25) — enrichissement du haystack des dossiers législatifs AN
avec le texte parlementaire complet (PDF du texte initial + rapports).

Problème résolu : avant P4, le matcher mots-clés ne voyait pour un dossier
AN que `title + libelles_haystack` (cumul des libellés d'actes ≤3 000 c).
L'exposé des motifs et le dispositif des articles — les seuls endroits
où apparaissent les termes thématiques (« EGalim », « SRP+10 », « Lidl »,
« JO 2024 »…) — n'étaient pas consultés. Conséquence : dossier dont le
titre officiel est générique (« Proposition de loi tendant à… ») était
manqué systématiquement.

Solution : pour chaque dossier AN, on scrape la page
`/dyn/17/dossiers/<uid>` pour récupérer la liste des liens texte
(`/dyn/17/textes/l17b<NUM>_<type>`) et des rapports
(`/dyn/17/rapports/<cion>/l17b<NUM>_rapport-fond`). On télécharge les
PDFs correspondants (≤2 textes + ≤2 rapports, pour borner la volumétrie),
on extrait le texte via pypdf (déjà utilisé en R35-B pour les CR AN), et
on concatène dans un `haystack_body` tronqué à `MAX_HAYSTACK_CHARS`.

Cache disque : `data/cache/dosleg_pdf/<uid>.txt`. Un fichier par dossier
pour éviter de re-télécharger à chaque run (le texte d'un PJL ne change
pas ; seuls les rapports évoluent). TTL : si le fichier existe, on ne
refetch pas — on considère le haystack stable. À forcer un refresh via
reset_category=dossiers_legislatifs si besoin.

Tolérant aux échecs : HTTP 404, timeout, PDF non parsable → on retourne
"" et on cache vide (marqueur) pour ne pas re-tenter à chaque run.
"""
from __future__ import annotations

import io
import logging
import re
import time
from pathlib import Path

from ._common import fetch_bytes

log = logging.getLogger("veille.sources.assemblee_dosleg_pdf")

_CACHE_DIR = Path("data/cache/dosleg_pdf")
_DOSSIER_URL = "https://www.assemblee-nationale.fr/dyn/17/dossiers/{uid}"

# On limite le volume à ~15 Ko de texte par dossier — largement assez
# pour que le matcher trouve les mots-clés thématiques dans l'exposé des
# motifs et l'article principal, sans exploser la DB.
MAX_HAYSTACK_CHARS = 15_000

# Nombre max de textes + rapports à télécharger par dossier. Évite de
# scanner 10+ rapports pour un gros PJL (souvent un seul texte initial +
# 1-2 rapports suffisent à couvrir le sujet côté matcher).
MAX_TEXTES_PER_DOSSIER = 2
MAX_RAPPORTS_PER_DOSSIER = 2

# Rate limiting : pause entre requêtes AN. Réduit de 300 ms à 100 ms
# (2026-04-25) pour ne pas plomber le cold-start. À 300 ms, 5 400 fetches
# = 27 min de pause pure → timeout du workflow GitHub Actions.
_REQUEST_DELAY_S = 0.1

# Budget de fetches PDF par run (cold-start étalé). 700 fetches × ~250 ms
# = ~3 min net + parsing → reste largement sous le timeout 25 min même
# avec le reste de la pipeline. Couvre ~175 dossiers par run au pire
# (4 PDF/dossier). Avec ~1300 dossiers à traiter au total, tout sera
# en cache en ~7-8 runs (≈ 1 semaine de daily). Les dossiers non
# traités ce run ne sont PAS cachés vides → ils seront retentés au run
# suivant.
_FETCH_BUDGET_PER_RUN = 700
_fetch_count = 0


def reset_fetch_budget() -> None:
    """Remet le compteur à zéro (utile en tests). Pas appelé en prod —
    le module se recharge à chaque process worker."""
    global _fetch_count
    _fetch_count = 0

# Pattern des liens internes du dossier :
#   /dyn/17/textes/l17b<NUM>_<type>          → texte parlementaire
#   /dyn/17/rapports/<cion>/l17b<NUM>_<type> → rapport de commission
_LINK_TEXTE_RE = re.compile(
    r"""href=["'](https?://(?:www\.)?assemblee-nationale\.fr)?"""
    r"""(/dyn/17/textes/l17[bt]\d+_[a-z0-9\-]+)["']""",
    re.IGNORECASE,
)
_LINK_RAPPORT_RE = re.compile(
    r"""href=["'](https?://(?:www\.)?assemblee-nationale\.fr)?"""
    r"""(/dyn/17/rapports/[a-z0-9\-]+/l17[bt]\d+_[a-z0-9\-]+)["']""",
    re.IGNORECASE,
)


def _pdf_to_text(pdf_bytes: bytes, label: str = "") -> str:
    """Extrait le texte d'un PDF via pypdf. Best-effort, retourne "" en cas d'échec."""
    try:
        from pypdf import PdfReader  # import local pour pas charger si jamais utilisé
    except ImportError:
        log.warning("pypdf non disponible, haystack PDF désactivé")
        return ""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        parts: list[str] = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                continue
        text = " ".join(parts)
        text = re.sub(r"\s+", " ", text).strip()
        return text
    except Exception as e:
        log.debug("PDF illisible %s : %s", label, e)
        return ""


def _cache_path(uid: str) -> Path:
    """Chemin du cache disque pour un dossier — {uid}.txt."""
    safe = re.sub(r"[^A-Za-z0-9_.\-]", "_", uid)[:80]
    return _CACHE_DIR / f"{safe}.txt"


def _load_cache(uid: str) -> str | None:
    """Lit le cache si présent. Retourne None si absent."""
    p = _cache_path(uid)
    if not p.exists():
        return None
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return None


def _save_cache(uid: str, text: str) -> None:
    """Persiste le texte (peut être vide — marqueur « déjà tenté »)."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    p = _cache_path(uid)
    try:
        p.write_text(text, encoding="utf-8")
    except Exception as e:
        log.debug("Cache write KO %s : %s", uid, e)


def _extract_links(html: str) -> tuple[list[str], list[str]]:
    """Extrait les URLs des textes et rapports depuis la page dossier HTML.

    Dédoublonne en préservant l'ordre d'apparition (le premier texte trouvé
    est le texte initial, les suivants sont les textes post-commission).
    """
    textes: list[str] = []
    rapports: list[str] = []
    seen: set[str] = set()
    for m in _LINK_TEXTE_RE.finditer(html):
        path = m.group(2)
        if path in seen:
            continue
        seen.add(path)
        textes.append(path)
    for m in _LINK_RAPPORT_RE.finditer(html):
        path = m.group(2)
        if path in seen:
            continue
        seen.add(path)
        rapports.append(path)
    return textes, rapports


def _fetch_pdf_text(path: str, label: str) -> str:
    """Télécharge le PDF `{path}.pdf` et extrait son texte."""
    if not path.startswith("/"):
        path = "/" + path
    url = f"https://www.assemblee-nationale.fr{path}.pdf"
    try:
        pdf_bytes = fetch_bytes(url)
    except Exception as e:
        log.debug("Fetch PDF KO %s : %s", url, e)
        return ""
    if not pdf_bytes or len(pdf_bytes) < 500:
        return ""
    # Sanity check : le Content-Type est parfois pas strictement application/pdf,
    # mais les 4 premiers octets sont '%PDF'. On garde large.
    if not pdf_bytes[:4].startswith(b"%PDF"):
        return ""
    return _pdf_to_text(pdf_bytes, label=label)


def fetch_pdf_haystack(uid: str, *, use_cache: bool = True) -> str:
    """Retourne le haystack PDF d'un dossier AN (texte initial + rapports).

    - Cache disque : si présent, relecture directe (pas de re-fetch).
    - Scrape la page dossier pour trouver les liens de textes et rapports.
    - Télécharge les PDF (max 2 textes + 2 rapports), extrait le texte,
      concatène et tronque à MAX_HAYSTACK_CHARS.
    - Budget _FETCH_BUDGET_PER_RUN : si épuisé, retourne "" SANS cacher
      pour retenter au prochain run (cold-start étalé sur ~7-8 runs).
    - En cas d'échec à n'importe quelle étape : retourne "" et cache vide.
    """
    global _fetch_count
    if not uid:
        return ""
    if use_cache:
        cached = _load_cache(uid)
        if cached is not None:
            return cached

    # Budget épuisé pour ce run : on n'écrit PAS de cache vide pour que
    # le dossier soit retenté au run suivant. Coût : 1 lookup cache disque
    # par dossier non-traité (négligeable).
    if _fetch_count >= _FETCH_BUDGET_PER_RUN:
        return ""

    url = _DOSSIER_URL.format(uid=uid)
    try:
        _fetch_count += 1
        html_bytes = fetch_bytes(url, impersonate=True)
        html = html_bytes.decode("utf-8", errors="ignore") if html_bytes else ""
    except Exception as e:
        log.debug("Fetch dossier HTML KO %s : %s", uid, e)
        _save_cache(uid, "")
        return ""
    if not html or "<title>" not in html:
        _save_cache(uid, "")
        return ""

    textes, rapports = _extract_links(html)
    if not textes and not rapports:
        _save_cache(uid, "")
        return ""

    parts: list[str] = []
    for path in textes[:MAX_TEXTES_PER_DOSSIER]:
        if _fetch_count >= _FETCH_BUDGET_PER_RUN:
            break
        time.sleep(_REQUEST_DELAY_S)
        _fetch_count += 1
        t = _fetch_pdf_text(path, f"{uid}:{path}")
        if t:
            parts.append(t)
    for path in rapports[:MAX_RAPPORTS_PER_DOSSIER]:
        if _fetch_count >= _FETCH_BUDGET_PER_RUN:
            break
        time.sleep(_REQUEST_DELAY_S)
        _fetch_count += 1
        t = _fetch_pdf_text(path, f"{uid}:{path}")
        if t:
            parts.append(t)

    haystack = " · ".join(parts)[:MAX_HAYSTACK_CHARS]
    # Cache même un haystack partiel (textes uniquement, rapports skipés
    # par budget). Au prochain run, on relit ce cache et le dossier est
    # déjà couvert — pas besoin de re-fetcher les rapports manquants.
    # Compromis : un dossier partiellement chargé ne bénéficiera pas des
    # rapports tant qu'on ne fait pas un reset_category=dossiers.
    _save_cache(uid, haystack)
    return haystack
