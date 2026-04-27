"""R35-B (2026-04-24) — Scraper des comptes rendus de commissions AN.

Ingère le corps complet (PDF → texte) des CR de commissions permanentes AN
pour alimenter le `haystack_body` du matcher keyword.

Contexte : le pipeline agenda (`assemblee._normalize_agenda`) capte les
RÉUNIONS de commission via `Agenda.json.zip` mais uniquement avec le
titre ODJ. Le CORPS du compte rendu n'est publié qu'a posteriori
(~quelques jours après la réunion) sous forme de PDF à l'URL :

    /dyn/17/comptes-rendus/{slug}/l17{slug}{SS}{NNN}_compte-rendu.pdf

où `{slug}` = slug court de la commission (ex. cion-cedu), `{SS}` =
année-session (ex. 2526 pour octobre 2025 → septembre 2026), `{NNN}` = n°
séquentiel de réunion dans la session.

Le numéro séquentiel {NNN} n'est PAS dans le JSON Agenda (compteRenduRef
est null pour les commissions). On itère donc par force brute par
commission, à partir du dernier n° connu (état persisté dans
data/an_cr_state.json). Les runs suivants ne refont qu'un petit delta.

Cas déclencheur (Cyril, R35-B) : commission cion-cedu réunion 58
(2026-04-22), « Table ronde sur la gouvernance des autres sports que le
football » — le titre agenda ne cite pas « sport » explicitement côté
ODJ, mais le PDF en cite 15+ occurrences. Avant R35-B : non matché.
Après : matché via haystack_body.
"""
from __future__ import annotations

import io
import json
import logging
import re
from datetime import datetime
from pathlib import Path

import httpx

from ..models import Item
from ._common import _client

log = logging.getLogger(__name__)

# Commissions permanentes AN 17e législature. Mapping slug → libellé long.
# Les slugs sont ceux exposés dans les URLs publiques /dyn/17/comptes-rendus/
# (convention AN). Liste figée ici volontairement : on ne scrape QUE les
# commissions qui sont susceptibles d'aborder le sport (toutes y touchent
# via PLF/PLFSS et auditions transverses). Pour élargir, étendre le dict.
# R36-A (2026-04-24) — ajout du groupe d'études Sport. Les GE publient leurs
# comptes rendus / bulletins sous le même chemin `/dyn/17/comptes-rendus/<slug>/`
# que les commissions, sur le portail AN. Cyril a confirmé le gap : les GE
# (Sport en priorité) n'étaient pas couverts. Le slug `ge-sport` est le slug
# officiel AN pour le groupe d'études Sport (vérifié sur /dyn/17/organes/ge-sport
# qui existe).
_DEFAULT_COMMISSIONS: dict[str, str] = {
    "cion-cedu":  "Commission des affaires culturelles et de l'éducation",
    "cion-soc":   "Commission des affaires sociales",
    "cion-etran": "Commission des affaires étrangères",
    "cion-def":   "Commission de la défense nationale et des forces armées",
    "cion-dvp":   "Commission du développement durable",
    "cion-eco":   "Commission des affaires économiques",
    "cion-fin":   "Commission des finances",
    "cion-lois":  "Commission des lois",
    # Instance Lidl : le GE Sport (ge-sport) est retiré — hors périmètre
    # grande distribution. Un GE dédié au commerce/distribution n'existe
    # pas à date côté AN XVIIe législature.
}

# State file : mémorise par session { slug: { last_num: int } }. Sans ça,
# un run CI scannerait intégralement 1..N pour les 8 commissions à chaque
# itération (~400 requêtes/run, la plupart en 404). Avec state : on reprend
# au dernier n° connu + delta court (≤ max_new_per_run).
STATE_PATH = Path("data/an_cr_state.json")

# Regex parsing
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_DATE_FR_RE = re.compile(
    r"(\d{1,2})\s+(janvier|février|mars|avril|mai|juin|juillet|août|"
    r"septembre|octobre|novembre|décembre)\s+(\d{4})",
    re.IGNORECASE,
)
_MOIS_FR = {
    "janvier": 1, "février": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "août": 8, "septembre": 9, "octobre": 10, "novembre": 11,
    "décembre": 12,
}


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("an_cr_state.json illisible (%s), reset", e)
        return {}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _session_code(d: datetime) -> str:
    """Code session AN : 'SS' pour année session-1 + 'SS' pour session.

    Session parlementaire AN : ouverture 1er octobre → clôture 30 septembre.
    Ex. octobre 2025 → septembre 2026 : session 2025-2026 → code "2526".
    """
    y = d.year
    if d.month >= 10:
        return f"{y % 100:02d}{(y + 1) % 100:02d}"
    return f"{(y - 1) % 100:02d}{y % 100:02d}"


def _extract_pdf_text(pdf_bytes: bytes, max_chars: int = 200000) -> str:
    """Extrait le texte brut d'un PDF avec pypdf, tronque à max_chars.

    Si pypdf indisponible (dépendance non installée), renvoie "" sans
    planter — le matcher retombe alors sur le titre du CR seul.

    R39-E (2026-04-25) : nettoyage du préambule institutionnel des CR AN
    (« 1 7 e L É G I S L A T U R E A S S E M B L É E N A T I O N A L E
    Compte rendu Commission des affaires économiques – Examen… »).
    PyPDF extrait les titres de page avec espacement caractère par
    caractère, bruit visible en début d'extrait. On coupe avant le
    premier mot de contenu réel.

    2026-04-26 (P1c-fix) : `max_chars` passé de 10 000 à 200 000.
    Cause racine du « 0 CR AN matché sur 6 mois » signalé par Cyril :
    sur les CR longs (60–180 k chars — examen de loi, longues auditions),
    les keywords GD (« grande distribution », « EGalim », « centrale
    d'achat ») apparaissent souvent au-delà de la position 10 000 dans
    le texte du PDF (ex. cion-eco#006 : pos 30 559 ; #030 : 19 450).
    Avec une troncature à 10 k, le matcher ne voyait jamais ces
    occurrences. 200 k couvre 99 % des CR observés (max constaté ~182 k
    sur cion-soc#030). Coût DB estimé : ~120 MB sur ~600 CR matchés,
    acceptable pour la SQLite cachée en CI.
    """
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        log.warning("an_cr_commissions : pypdf non installé, pas d'extraction")
        return ""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception as e:
        log.debug("pypdf parse KO: %s", e)
        return ""
    out: list[str] = []
    total = 0
    for p in reader.pages:
        try:
            t = p.extract_text() or ""
        except Exception:
            t = ""
        if not t:
            continue
        out.append(t)
        total += len(t)
        if total >= max_chars:
            break
    merged = re.sub(r"\s+", " ", " ".join(out)).strip()
    merged = _strip_an_pdf_preamble(merged)
    return merged[:max_chars]


# R39-E (2026-04-25) — détection du préambule institutionnel des CR AN PDF.
# PyPDF rend les titres de page sous la forme « 1 7 e L É G I S L A T U R E
# A S S E M B L É E N A T I O N A L E Compte rendu Commission des
# affaires économiques – Examen… ». On reconnaît le début du préambule
# (séquence avec « LÉGISLATURE » lettre par lettre), puis on cherche le
# premier verbe de contenu pour couper.
#
# Approche : pattern simple sur le DÉBUT de string (« d-d-e LÉGISLATURE
# … Compte rendu Commission ») pour vérifier qu'on a bien un préambule,
# puis on coupe à la première occurrence d'un verbe d'examen/audition.
_AN_PREAMBLE_START_RE = re.compile(
    r"^\s*\d\s*\d?\s*e\s+L\s*[EÉ]\s*G\s*I\s*S\s*L\s*A\s*T\s*U\s*R\s*E\s+"
    r"A\s*S\s*S\s*E\s*M\s*B\s*L\s*[EÉ]\s*E\s+N\s*A\s*T\s*I\s*O\s*N\s*A\s*L\s*E\s+"
    r"Compte\s+rendu\s+Commission",
    re.IGNORECASE,
)
# Mots-clés qui marquent le début du contenu réel (premier verbe).
_AN_CONTENT_START_RE = re.compile(
    r"\b(Examen|Audition|Mission|Communication|Table|Présidence|"
    r"Réunion|Constitution|Désignation|Discussion|Suite)\b",
)


def _strip_an_pdf_preamble(text: str) -> str:
    """Retire l'entête institutionnelle des CR AN extraits via pypdf.

    Idempotent : si le préambule n'est pas détecté ou si aucun verbe de
    contenu n'est trouvé après, retourne le texte inchangé.
    """
    if not text:
        return text
    if _AN_PREAMBLE_START_RE.match(text) is None:
        return text
    # Préambule détecté : on cherche le premier verbe de contenu et on
    # coupe AVANT.
    m = _AN_CONTENT_START_RE.search(text)
    if m is None or m.start() < 50:
        # Pas de verbe de contenu dans une zone plausible — on garde le
        # texte tel quel.
        return text
    return text[m.start():].strip()


def _parse_title(html_text: str, commission_label: str, num: int) -> str:
    """Titre humain depuis <title> HTML. Formate en 'Commission — n° X'."""
    m = _TITLE_RE.search(html_text)
    if not m:
        return f"CR {commission_label} — n° {num}"[:220]
    raw = re.sub(r"\s+", " ", m.group(1)).strip()
    # Titre AN type : "Compte rendu de réunion n° 58 - Commission des affaires
    # culturelles et de l'éducation - Session 2025 – 2026 - 17e législature -
    # Assemblée nationale". On garde les 2 premières sections (CR + comm).
    parts = [p.strip() for p in raw.split(" - ")]
    if len(parts) >= 2:
        return f"{parts[0]} — {parts[1]}"[:220]
    return raw[:220]


def _parse_date(text: str) -> datetime | None:
    """Première date FR trouvée dans le texte (ex. '22 avril 2026')."""
    m = _DATE_FR_RE.search(text or "")
    if not m:
        return None
    day_s, mois_s, year_s = m.groups()
    mois = _MOIS_FR.get(mois_s.lower())
    if mois is None:
        return None
    try:
        return datetime(int(year_s), mois, int(day_s))
    except ValueError:
        return None


def _fetch_silent(url: str, timeout: float = 20.0) -> tuple[int, bytes]:
    """GET silencieux : renvoie (status, bytes) sans lever sur 4xx.

    On NE passe PAS par `_common.fetch_bytes` pour éviter les logs ERROR
    massifs sur les 404 attendus du brute-force (plusieurs dizaines par run).
    """
    try:
        with _client() as c:
            r = c.get(url, timeout=timeout)
            return r.status_code, r.content
    except httpx.RequestError as e:
        log.debug("GET %s : erreur réseau %s", url, e)
        return 0, b""


def _fetch_cr(slug: str, session: str, num: int,
              commission_label: str) -> tuple[Item | None, bool]:
    """Tente de récupérer un CR (slug, session, num).

    Renvoie un tuple `(item, has_body)` :
      - `item` : Item ingéré, ou None si la page HTML est introuvable (404).
      - `has_body` : True si le PDF a été récupéré ET extrait avec succès
        (body non trivialement vide). False si HTML 200 mais PDF 404 ou body
        vide — l'item est créé quand même mais avec haystack_body="".

    2026-04-27 — `has_body` exposé pour permettre à `fetch_source` de NE PAS
    marquer le numéro comme `scanned` quand le PDF n'est pas encore publié.
    Cas réel : audition Dominique Schelcher (14/04/2026, cion-eco N076)
    annoncée à l'agenda mais dont le PDF n'a été publié que 1-2 semaines
    après la séance. Le scraper voyait HTML 200 + PDF 404 → ingérait un
    Item à body vide → matched_keywords=[] → exclu du site, MAIS num
    marqué scanned → jamais ré-ingéré quand le PDF est apparu.
    """
    base = (
        f"https://www.assemblee-nationale.fr/dyn/17/comptes-rendus/"
        f"{slug}/l17{slug}{session}{num:03d}_compte-rendu"
    )
    html_url = base
    pdf_url = base + ".pdf"

    # 1) Vérifier que la page HTML existe (200). 404 → CR pas publié.
    status, html_content = _fetch_silent(html_url)
    if status != 200 or not html_content:
        return None, False
    html_text = html_content.decode("utf-8", errors="replace")

    # 2) Récupérer le PDF (source du corps). Best-effort : si 404 ou erreur,
    #    on expose quand même un item avec haystack_body vide plutôt que de
    #    rien renvoyer (la page HTML existe, donc le CR est référencé). Mais
    #    on retourne `has_body=False` pour indiquer qu'il faudra ré-essayer
    #    au prochain run (le PDF est probablement publié en différé).
    pdf_status, pdf_bytes = _fetch_silent(pdf_url, timeout=30.0)
    body = _extract_pdf_text(pdf_bytes) if pdf_status == 200 else ""
    has_body = pdf_status == 200 and len(body) >= 200

    # 3) Date : on cherche d'abord dans le PDF (page de garde), sinon dans
    #    le HTML, sinon on pose "aujourd'hui" (le CR vient d'être publié).
    dt = (_parse_date(body[:2000] if body else "")
          or _parse_date(html_text)
          or datetime.utcnow().replace(microsecond=0))

    title = _parse_title(html_text, commission_label, num)

    # UID stable : ne se basera jamais sur le titre (qui pourrait varier
    # si le CR est republié avec une refonte AMO).
    uid = f"an-cr-{slug}-{session}-{num:03d}"

    # Summary : début du corps pour l'affichage site (fallback: titre).
    summary = (body[:2000] if body else title).strip()

    item = Item(
        source_id="an_cr_commissions",
        uid=uid,
        category="comptes_rendus",
        chamber="AN",
        title=title,
        url=html_url,
        published_at=dt,
        summary=summary,
        raw={
            "path": "an_cr_commissions",
            "slug": slug,
            "session": session,
            "num": num,
            "pdf_url": pdf_url,
            # Exposé au KeywordMatcher (cf. keywords.apply, R26) :
            "haystack_body": body,
        },
    )
    return item, has_body


def fetch_source(src: dict) -> list[Item]:
    """Scrape les CR de commissions AN par force brute incrémentale.

    Paramètres supportés dans src :
      - commissions     : dict {slug: label} (défaut : _DEFAULT_COMMISSIONS)
                          ou liste de slugs (labels = slug).
      - session         : code session (ex. "2526"). Défaut : déduit de
                          la date courante.
      - max_new_per_run : nb max de CR nouveaux scrapés par commission
                          par run (défaut 10).
      - miss_tolerance  : nb de 404 consécutifs avant d'arrêter une commission
                          (défaut 5).
      - max_num         : n° max absolu testé (garde-fou, défaut 99).

    R37-B (2026-04-24) — stratégie de scan inversée. On descend depuis
    `max_num` vers le bas et on s'arrête dès qu'on a attrapé `max_new`
    CR OU qu'on a enchaîné `miss_tolerance` 404 consécutifs DANS la zone
    déjà vue. Ça attrape les CR les plus récents en priorité et évite
    l'effet « scraper coincé au n°10 parce que le state n'est pas
    persisté et que max_new=10 limite le progrès ». Cyril (2026-04-24) :
    le CR 58 de cion-cedu manquait en prod parce que le scan ascendant
    repartait de 0 à chaque run et butait sur miss_tolerance=3 après
    quelques numéros absents.
    """
    raw_comm = src.get("commissions") or _DEFAULT_COMMISSIONS
    if isinstance(raw_comm, list):
        commissions = {s: s for s in raw_comm}
    else:
        commissions = dict(raw_comm)

    max_new = int(src.get("max_new_per_run", 10))
    miss_tolerance = int(src.get("miss_tolerance", 5))
    max_num = int(src.get("max_num", 99))
    session = str(src.get("session") or _session_code(datetime.utcnow()))

    state = _load_state()
    session_state = state.setdefault(session, {})
    items: list[Item] = []

    for slug, label in commissions.items():
        slug_state = session_state.setdefault(slug, {
            "last_num": 0,       # plus grand num jamais vu (historique)
            "scanned": [],       # nums déjà ingérés, pour skip rapide
        })
        scanned = set(slug_state.get("scanned") or [])
        num = max_num
        miss = 0
        new_count = 0
        local_max = slug_state.get("last_num", 0)
        # Scan descendant : du plus récent (max_num) vers le plus ancien.
        # On skip les nums déjà vus (scanned) pour ne pas refaire 99 →
        # déjà en DB. Les misses consécutifs s'accumulent ; dès qu'on
        # en enchaîne `miss_tolerance`, on arrête (on est sorti de la
        # zone des CR publiés pour cette session).
        while num >= 1 and miss < miss_tolerance and new_count < max_new:
            if num in scanned:
                # déjà ingéré lors d'un run antérieur → on le saute sans
                # consommer de miss (ce n'est pas un 404)
                num -= 1
                continue
            result = _fetch_cr(slug, session, num, label)
            if result is None:
                # Compat ascendante (anciennes signatures) : si _fetch_cr
                # retourne None directement, c'est un 404.
                miss += 1
                num -= 1
                continue
            it, has_body = result
            if it is not None:
                items.append(it)
                # 2026-04-27 — on ne marque comme `scanned` QUE les CR dont
                # le PDF a été récupéré et extrait avec succès (>= 200 chars).
                # Sans ce garde-fou, un CR dont le HTML est publié AVANT le
                # PDF (cas réel cion-eco N076 audition Schelcher 14/04/2026)
                # est ingéré avec haystack_body="" puis marqué scanned →
                # jamais ré-ingéré quand le PDF arrive plus tard. Désormais,
                # tant que `has_body` reste False, on ré-essaie au run
                # suivant. Coût négligeable : 1 GET HTML + 1 GET PDF par
                # CR pending par run.
                if has_body:
                    scanned.add(num)
                if num > local_max:
                    local_max = num
                new_count += 1
                miss = 0
            else:
                miss += 1
            num -= 1
        slug_state["last_num"] = local_max
        # On borne la liste scanned pour éviter une croissance indéfinie
        # dans le JSON d'état : on garde les 200 derniers (largement plus
        # que le nombre de réunions par commission par session, 50-80).
        slug_state["scanned"] = sorted(scanned)[-200:]
        session_state[slug] = slug_state
        log.info(
            "an_cr_commissions %s session=%s : +%d items "
            "(last_num=%d, scanned=%d)",
            slug, session, new_count, local_max, len(scanned),
        )

    _save_state(state)
    return items
