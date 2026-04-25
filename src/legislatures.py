"""P7 (2026-04-25) — gestion agnostique des législatures AN.

Problème résolu : les URLs des dumps open data AN contiennent le numéro
de la législature en dur (`.../repository/17/...`). À la bascule 17→18
(élections ou dissolution), il fallait toucher la config et risquer de
perdre la couverture des 3 mois / 3 ans glissants pendant la transition.

Approche : une table hardcodée des législatures françaises (numéro + date
de début + date de fin) et un helper `active_legislatures(since_days)`
qui renvoie la liste des numéros à couvrir pour une fenêtre donnée. La
config `sources.yml` expose des `url_template` contenant `{legislature}`,
et `normalize.iter_sources()` expanse en N sources concrètes (une par
législature active).

Pour ajouter la 18e : ajouter une ligne `(18, date(2029, 6, 10), None)`.
Aucun autre code à toucher.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class Legislature:
    """Une législature française : numéro + période."""
    num: int
    start: date
    end: date | None  # None = législature en cours

    def covers(self, d: date) -> bool:
        if d < self.start:
            return False
        if self.end is not None and d > self.end:
            return False
        return True


# Table officielle des législatures françaises contemporaines.
# Sources : site de l'Assemblée nationale, calendriers officiels.
# À étendre à la prochaine bascule : `Legislature(18, date(YYYY, M, D), None)`
# et passer `end=date(...)` sur l'entrée 17e.
LEGISLATURES: tuple[Legislature, ...] = (
    Legislature(num=15, start=date(2017, 6, 20), end=date(2022, 6, 21)),
    Legislature(num=16, start=date(2022, 6, 28), end=date(2024, 6, 8)),
    Legislature(num=17, start=date(2024, 7, 18), end=None),
)


def active_legislatures(since_days: int, *, today: date | None = None) -> list[int]:
    """Renvoie la liste des numéros de législatures à couvrir pour capter
    les dossiers/textes des `since_days` derniers jours.

    Exemples (à la date du 2026-04-25) :
        active_legislatures(30)    → [17]
        active_legislatures(365)   → [17]
        active_legislatures(1095)  → [16, 17]  # fenêtre 3 ans → déborde sur 16e
        active_legislatures(3650)  → [15, 16, 17]

    La liste est triée par numéro ascendant (ancien → récent).
    """
    ref = today or date.today()
    cutoff = ref - timedelta(days=max(since_days, 0))
    nums: list[int] = []
    for leg in LEGISLATURES:
        # La législature est pertinente si elle n'a pas fini avant le
        # cutoff ET si elle a déjà commencé au plus tard aujourd'hui.
        if leg.end is not None and leg.end < cutoff:
            continue
        if leg.start > ref:
            continue
        nums.append(leg.num)
    return sorted(nums)


def current_legislature(*, today: date | None = None) -> int:
    """Renvoie le numéro de la législature en cours."""
    ref = today or date.today()
    for leg in LEGISLATURES:
        if leg.covers(ref):
            return leg.num
    # Fallback : la plus récente qui a déjà commencé
    candidates = [leg for leg in LEGISLATURES if leg.start <= ref]
    if candidates:
        return max(candidates, key=lambda x: x.start).num
    return LEGISLATURES[-1].num
