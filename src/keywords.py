"""Matching des mots-clés — normalisation accents + casse.

Lidl v1 (2026-04-24) : ajout du mode « contextual ». Deux formats
acceptés dans config/keywords.yml :

  1. String simple
       - Lidl
       - EGalim
     → équivalent à { term: "<val>", mode: "direct" }. C'est la
     rétro-compat avec l'instance sport.

  2. Objet avec mode explicite
       - { term: "Carrefour", mode: "contextual",
           requires_any: ["groupe Carrefour", "hypermarché", "Bompard",
                          "grande distribution", "EGalim", "SRP+10"] }

Un terme en mode « contextual » n'est retenu dans matched_keywords
que si au moins un des termes listés dans requires_any est présent
dans le haystack. Si requires_any est omis, le fallback est : au
moins un autre terme en mode « direct » doit matcher dans le même
texte.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import yaml
from unidecode import unidecode


from . import textclean as _textclean  # noqa: E402


def _clean_html(text: str) -> str:
    """Strip HTML tags + décode entités + collapse whitespace."""
    return _textclean.strip_html(text)


def _normalize(text: str) -> str:
    """Minuscules, sans accent, espaces simples."""
    if not text:
        return ""
    text = unidecode(text).lower()
    text = re.sub(r"\s+", " ", text).strip()
    return text


@dataclass
class _KeywordEntry:
    term: str                   # forme originale pour affichage
    family: str                 # nom de la famille YAML
    mode: str = "direct"        # "direct" | "contextual"
    requires_any: list[str] = field(default_factory=list)  # normalisés


def _parse_entry(raw, family: str) -> _KeywordEntry:
    """Accepte soit str (direct), soit dict {term, mode, requires_any}."""
    if isinstance(raw, str):
        return _KeywordEntry(term=raw, family=family, mode="direct")
    if isinstance(raw, dict) and "term" in raw:
        mode = raw.get("mode", "direct").lower()
        if mode not in ("direct", "contextual"):
            raise ValueError(
                f"Mode inconnu '{mode}' pour le terme {raw.get('term')!r} "
                f"(famille {family!r}) — attendu 'direct' ou 'contextual'"
            )
        requires_any = [
            _normalize(x) for x in (raw.get("requires_any") or [])
            if isinstance(x, str) and x.strip()
        ]
        if mode == "contextual" and not requires_any:
            # Toléré : fallback sur présence d'un autre terme direct
            pass
        return _KeywordEntry(
            term=raw["term"].strip(),
            family=family,
            mode=mode,
            requires_any=requires_any,
        )
    raise ValueError(
        f"Entrée YAML invalide en famille {family!r}: {raw!r}"
    )


class KeywordMatcher:
    def __init__(self, path: str | Path):
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))

        # Parse toutes les entrées et les regroupe par terme normalisé.
        # « First wins » comme avant : quand plusieurs variantes se
        # normalisent pareil, on garde la première rencontrée.
        self.index: dict[str, _KeywordEntry] = {}
        # Conservé pour introspection / tests — liste par famille
        self.families: dict[str, list[str]] = {}
        for family, items in (raw or {}).items():
            self.families.setdefault(family, [])
            for raw_item in items or []:
                entry = _parse_entry(raw_item, family)
                key = _normalize(entry.term)
                if key and key not in self.index:
                    self.index[key] = entry
                self.families[family].append(entry.term)

        # Corpus de termes requires_any (context markers) qui ne sont
        # pas eux-mêmes dans l'index : on les ajoute au pattern pour
        # que la détection soit efficace (findall unique), mais on ne
        # les exposera jamais dans matched_keywords.
        context_markers: set[str] = set()
        for entry in self.index.values():
            for marker in entry.requires_any:
                if marker and marker not in self.index:
                    context_markers.add(marker)
        self._context_markers: set[str] = context_markers

        # Deux patterns distincts pour éviter qu'un match long avale un
        # terme plus court qu'il contient. Ex. haystack « groupe carrefour
        # face à srp+10 » doit détecter « carrefour » (matchable
        # contextual) ET « groupe carrefour » (context marker) : si l'on
        # utilisait un seul pattern OR avec tri par longueur décroissante,
        # le regex consommerait « groupe carrefour » d'un bloc et passerait
        # à côté de « carrefour » seul.
        self._pattern_matchable = self._build_pattern(self.index.keys())
        self._pattern_markers = self._build_pattern(context_markers)
        # Alias conservé pour la recherche du snippet (qui a juste besoin
        # d'une position de match, peu importe laquelle).
        self._pattern = self._build_pattern(
            list(self.index.keys()) + list(context_markers)
        )

    @staticmethod
    def _build_pattern(terms) -> re.Pattern:
        terms_sorted = sorted({t for t in terms if t}, key=len, reverse=True)
        if not terms_sorted:
            return re.compile(r"(?!)")
        escaped = [re.escape(t) for t in terms_sorted]
        return re.compile(
            r"(?<![a-z0-9])(" + "|".join(escaped) + r")(?![a-z0-9])"
        )

    # ------------------------------------------------------------------
    def _has_any_direct_in(self, found_raw: set[str]) -> bool:
        """Y a-t-il au moins un terme direct matché dans found_raw ?"""
        for t in found_raw:
            entry = self.index.get(t)
            if entry and entry.mode == "direct":
                return True
        return False

    def match(self, *texts: str) -> tuple[list[str], list[str]]:
        """Renvoie (mots-clés matchés, familles uniques).

        Un terme contextual n'est retenu que si au moins un de ses
        requires_any est présent dans le même haystack (ou, si la
        liste requires_any est vide, si un terme direct est matché
        ailleurs dans le texte).
        """
        haystack = _normalize(" ".join(t or "" for t in texts))
        if not haystack:
            return [], []
        # Matchables (direct + contextual) et context markers sont
        # cherchés avec 2 patterns distincts pour ne pas se cannibaliser.
        found_matchable = set(self._pattern_matchable.findall(haystack))
        found_markers = set(self._pattern_markers.findall(haystack))
        if not found_matchable:
            return [], []

        matched: list[str] = []
        families: set[str] = set()
        has_direct = self._has_any_direct_in(found_matchable)

        for t in found_matchable:
            entry = self.index.get(t)
            if entry is None:
                continue
            if entry.mode == "direct":
                matched.append(entry.term)
                if entry.family:
                    families.add(entry.family)
                continue
            # Contextual : vérifier le contexte
            if entry.requires_any:
                # Un requires_any est validé s'il est détecté soit via
                # les markers, soit par contains direct dans haystack
                # (ex. un requires_any multi-mots non préenregistré).
                ok = any(
                    m in found_markers or m in found_matchable or m in haystack
                    for m in entry.requires_any
                )
            else:
                ok = has_direct and any(
                    other != t and self.index.get(other, _KeywordEntry("", "")).mode == "direct"
                    for other in found_matchable
                )
            if ok:
                matched.append(entry.term)
                if entry.family:
                    families.add(entry.family)

        return sorted(set(matched)), sorted(families)

    # ------------------------------------------------------------------
    def recapitalize(self, keywords: Iterable[str]) -> list[str]:
        """Remappe chaque kw déjà matché sur sa forme affichable du yaml.

        Préserve l'ordre d'apparition et déduplique. Idempotent.
        """
        out: list[str] = []
        seen: set[str] = set()
        for kw in keywords or []:
            entry = self.index.get(_normalize(kw))
            canonical = entry.term if entry else kw
            if canonical not in seen:
                seen.add(canonical)
                out.append(canonical)
        return out

    # ------------------------------------------------------------------
    def build_snippet(self, original_text: str,
                      matched_keywords: list[str] | None = None,
                      window: int | None = None,
                      max_len: int = 800) -> str:
        """Extrait une phrase contenant le 1er mot-clé trouvé.

        2026-04-26 (port Bump R40-I sport) : si `matched_keywords` est
        fourni, on construit un mini-pattern restreint à ces termes pour
        que le snippet soit centré sur la 1re occurrence d'un MOT-CLÉ
        RÉELLEMENT RETENU par le matcher — pas sur un context marker
        seul (ex. « grande distribution » qui valide « Carrefour »
        contextuel mais qui, avant ce fix, pouvait à lui tout seul piloter
        l'ancrage du snippet alors que le vrai signal pertinent du texte
        était ailleurs). Fallback sur le pattern global si aucun
        matched_keyword n'apparaît dans `original_text` (cas où le match
        venait de raw.haystack_body et pas du summary).
        """
        original_text = _clean_html(original_text)
        if not original_text:
            return ""
        effective_window = max(window or 0, max_len // 2)
        haystack_norm = _normalize(original_text)

        # Mini-pattern restreint aux matched_keywords (priorité au fix R40-I).
        m = None
        if matched_keywords:
            normed = sorted(
                {_normalize(kw) for kw in matched_keywords if kw and _normalize(kw)},
                key=len, reverse=True,
            )
            if normed:
                escaped = "|".join(re.escape(t) for t in normed)
                kw_pattern = re.compile(
                    r"(?<![a-z0-9])(" + escaped + r")(?![a-z0-9])"
                )
                m = kw_pattern.search(haystack_norm)
        # Fallback : pattern global (matchable + context markers).
        if m is None:
            m = self._pattern.search(haystack_norm)
        if not m:
            return original_text.strip()[: max_len].strip()
        pos = m.start()
        end = m.end()
        start_cut = max(0, pos - effective_window)
        end_cut = min(len(original_text), end + effective_window)

        back_limit = max(0, pos - effective_window)
        min_back_span = int(effective_window * 0.6)
        for boundary in re.finditer(r"[\.\!\?\n]\s+", original_text[back_limit:pos]):
            candidate_start = back_limit + boundary.end()
            if pos - candidate_start >= min_back_span:
                start_cut = candidate_start
                break

        fwd_limit = min(len(original_text), end + effective_window)
        fwd_match = re.search(r"[\.\!\?](?:\s|$)", original_text[end:fwd_limit])
        if fwd_match:
            candidate_end = end + fwd_match.end()
            approx_len = candidate_end - start_cut
            if approx_len >= int(max_len * 0.6):
                end_cut = candidate_end

        snippet = original_text[start_cut:end_cut].strip()
        if len(snippet) > max_len:
            snippet = snippet[:max_len].rstrip() + "…"
        prefix = "…" if start_cut > 0 else ""
        suffix = "…" if end_cut < len(original_text) and not snippet.endswith(("…", ".", "!", "?")) else ""
        return (prefix + snippet + suffix).replace("\n", " ").strip()

    # ------------------------------------------------------------------
    def apply(self, items: Iterable):
        """Annote in-place une liste d'Item (matched_keywords + snippet).

        Si `item.raw` contient `haystack_body` (JORF NOTICE+CID, CR AN)
        ou `libelles_haystack` (R36-E — cumul des libellés d'actes d'un
        dossier AN), on les ajoute au match pour capter les textes au
        titre générique mais au corps pertinent.

        2026-04-26 (port Bump R40-I sport) : le snippet est centré sur
        la 1re occurrence d'un MATCHED keyword. Si aucun matched ne
        figure dans `summary`, on retombe sur `haystack_body` (PDF du
        CR / JORF) pour que l'extrait affiché sur le site contienne
        bien la phrase qui a déclenché le match — sinon le lecteur
        voyait un summary générique (« Compte rendu n° 75 — Présidence
        de M. Travert ») sans rapport visible avec le keyword affiché.
        """
        for item in items:
            extras: list[str] = []
            raw = getattr(item, "raw", None)
            if isinstance(raw, dict):
                hb = raw.get("haystack_body") or ""
                if hb:
                    extras.append(hb)
                lh = raw.get("libelles_haystack") or ""
                if lh:
                    extras.append(lh)
            kws, fams = self.match(item.title, item.summary, *extras)
            item.matched_keywords = kws
            item.keyword_families = fams

            # Snippet : on cherche d'abord dans summary (lisible). Si aucun
            # matched_keyword n'y figure mais que le match vient de
            # haystack_body, on centre l'extrait sur le PDF/CR pour donner
            # un contexte exploitable au lecteur.
            snippet_source = item.summary or item.title or ""
            if kws and snippet_source:
                norm_source = _normalize(snippet_source)
                if not any(_normalize(k) in norm_source for k in kws if k):
                    # Aucun matched dans summary → essayer haystack_body.
                    for extra in extras:
                        if not extra:
                            continue
                        norm_extra = _normalize(extra)
                        if any(_normalize(k) in norm_extra for k in kws if k):
                            snippet_source = extra
                            break
            item.snippet = self.build_snippet(
                snippet_source, matched_keywords=kws,
            )
        return items
