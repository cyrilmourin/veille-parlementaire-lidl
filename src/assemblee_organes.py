"""Whitelist d'organes AN/Sénat pertinents pour la veille Lidl.

Même mécanique que l'instance sport : certains organes (commissions,
groupes d'études, missions d'info, commissions d'enquête) traitent
régulièrement de grande distribution, EGalim, urbanisme commercial ou
travail dominical sans que le libellé de la réunion ne contienne un
mot-clé du lexique. Le bypass `_apply_organe_bypass` (main.py) injecte
alors le pseudo-keyword `(organe GD/commerce)` pour que l'item remonte.

État initial : whitelist VIDE. Le lexique Lidl est suffisamment riche
(EGalim, SRP+10, CDAC, enseignes, centrales d'achat, négociations
commerciales, Nutri-score…) pour capter l'essentiel via le matching
keyword standard. À peupler à la recette sur la base de
`data/amo_resolved.json` :

Codes candidats (à vérifier en live, cf. audit recette) :
- Commission des affaires économiques AN
- Commission du développement durable et aménagement du territoire AN
  (CDAC, ZAN, friches)
- Commission des affaires économiques Sénat (PO78718 pressenti)
- Commission de l'aménagement du territoire et développement durable
  Sénat (PO78664 pressenti)
- Groupe d'études commerce et grande distribution (s'il existe sous
  la législature 17)

La mécanique est conservée intacte pour qu'un simple ajout dans
`GD_RELEVANT_ORGANES` suffise à activer la remontée élargie.
"""
from __future__ import annotations

# Codes PO d'organes dont toute réunion / activité agenda doit remonter
# même sans match keyword dans le titre. Vide par défaut pour Lidl.
GD_RELEVANT_ORGANES: set[str] = set()

# Alias historique : certains modules importent encore `SPORT_RELEVANT_ORGANES`
# (hérité du projet sport). Aliasé pour ne pas casser les imports — valeur
# identique à la whitelist vide.
SPORT_RELEVANT_ORGANES = GD_RELEVANT_ORGANES

# Libellé injecté comme pseudo-keyword pour les items passant par le
# bypass organe. Visible côté site comme un kw-tag.
BYPASS_ORGANE_LABEL = "(organe GD/commerce)"


def is_gd_relevant_organe(organe_ref: str | None) -> bool:
    """True si le code organe est dans la whitelist grande distribution."""
    if not organe_ref:
        return False
    return organe_ref.strip() in GD_RELEVANT_ORGANES


# Alias compat avec les imports existants du projet sport.
is_sport_relevant_organe = is_gd_relevant_organe
