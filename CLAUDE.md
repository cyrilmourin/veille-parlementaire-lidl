# CLAUDE.md — Veille parlementaire Lidl

Repo de production servant `https://veille-lidl.sideline-conseil.fr` et l'envoi
quotidien à `ANOUCK.PAUMARD@lidl.fr`. Lu automatiquement par Claude Code à
chaque session ouverte dans ce dossier.

## Documents de référence (à lire dans cet ordre)

@HANDOFF.md
@README.md

`HANDOFF.md` est le document vivant : état actuel, décisions clés (ligne
éditoriale, matching contextuel, infra), TODO, pièges connus, historique. Il
est cumulatif et doit être mis à jour à la fin de chaque session de travail
significative. `README.md` reste figé sur l'architecture et la mise en prod
initiale (DNS, secrets, etc.).

## Règles projet — non-négociables

- **Étanchéité Sport / Lidl absolue.** Ce repo (`veille-parlementaire-lidl`)
  ne doit JAMAIS partager de credentials, de PAT, ni de commits avec le repo
  jumeau `veille-parlementaire-sport`. Le PAT fine-grained Lidl est dans
  `~/Documents/Claude/Projects/Veille Parlementaire/.github_pat.rtf` et n'est
  utilisable QUE pour ce repo.
- **Pas de commentaires `#` dans les blocs shell** que tu proposes à
  l'utilisateur (zsh sans `INTERACTIVE_COMMENTS` casse). Mettre les
  explications en prose au-dessus du bloc.
- **Toujours convertir les dates relatives en dates absolues** avant de les
  écrire en mémoire ou dans `HANDOFF.md` ("jeudi" → `2026-XX-XX`).

## Commandes utiles

Installation et tests :
```
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Run local (sans envoi mail, sans écriture site) :
```
python -m src.main dry -v
```

Run local complet sur les 7 derniers jours, sans email :
```
python -m src.main run --since 7 --no-email -v
```

Catch-up Lidl (rattrapage marque seule sur 18 mois — utilisé en CI uniquement) :
```
python -m src.main run --since 540 --catchup-lidl --no-email -v
```

Build du site Hugo en local pour preview :
```
cd site && hugo server -D
```

Déclencher un run en prod (workflow GitHub Actions) :
```
gh workflow run daily.yml
gh workflow run daily.yml -f reset_db=1 -f since_days=180
gh workflow run daily.yml -f reset_category=comptes_rendus
```

## Pipeline — données et caches

- **DB SQLite** : `data/veille.sqlite3` — persistée en CI via `actions/cache`,
  pas via git (gitignorée). Pour la purger localement : `rm
  data/veille.sqlite3`. En CI : `gh workflow run daily.yml -f reset_db=1`.
- **State scrapers incrémentaux** : `data/an_cr_state.json` (CR commissions
  AN). À supprimer en même temps qu'un `reset_category=comptes_rendus`, sinon
  les num scannés restent en `scanned` et les CR purgés ne reviennent pas
  (R39-M, déjà câblé dans `scripts/reset_category.py`).
- **Caches AMO** : `data/amo_resolved.json`,
  `data/an_texte_to_dossier.json`, `data/an_texte_to_libelles.json` —
  régénérés par `scripts/refresh_amo_cache.py`. Versionnés.
- **Cache photos sénateurs** : `data/senat_slugs.json` (108 KB, 348
  sénateurs). Versionné depuis 2026-04-26 (sans lui les chips photos
  questions Sénat sont vides). Régénération : `python -m
  scripts.build_senat_slugs`.
- **Cache PDF dosleg AN** : `data/cache/dosleg_pdf/` — gitignoré (gros).

## Lexique et matching

- `config/keywords.yml` : deux modes — `direct` (terme retenu dès apparition,
  ex. `Lidl`, `EGalim`, `SRP+10`) et `contextual` (validé par `requires_any`,
  ex. `Carrefour`, `Distributeurs`).
- **Règle d'or** : un `requires_any` ne doit contenir QUE des signaux
  distinctifs (nom d'enseigne précis, marqueur GD spécifique). Jamais
  `grande distribution` seule comme validateur — sur un long haystack, deux
  termes éloignés produisent du bruit (cf. faux positif "char Leclerc"
  documenté dans HANDOFF.md "Pièges connus").
- **EGalim n'est jamais un validateur de contexte** pour `Agriculture`,
  `Agriculteurs`, `Contrats amont`, `Matière première agricole`.

## Avant de modifier le matching ou un parser

Lire `HANDOFF.md` § "Pièges connus" en priorité. En particulier :
- Cache DB vs lexique : un changement de keyword n'invalide PAS les items
  déjà matchés en DB. Pour propager : `reset_db=1` ou `reset_category=<cat>`.
- Hardlinks cowork ↔ git : si tu fais des opérations git en bulk, fais-les
  depuis un clone propre (`/tmp/...`) plutôt que dans le workspace cowork.

## Bascule de législature 17 → 18

Procédure documentée dans `HANDOFF.md` § "Bascule de législature". P7
(URLs agnostiques) couvre `config/sources.yml` mais ~109 occurrences `17` /
`L17` restent codées en dur dans le code Python — checklist sed manuelle
prévue.

## Fichiers à NE PAS modifier sans raison forte

- `data/senat_slugs.json` — 108 KB, généré par script dédié, ne pas éditer
  à la main.
- `site/static/lidl-logo.png`, `site/static/favicon*.png` — assets fixes du
  co-branding client.
- `data/last_digest.html` — produit par `digest.py`, écrasé à chaque run.
- `Lidl-Rxx` dans `src/site_export.py` (`SYSTEM_VERSION_LABEL`) — bumper
  uniquement lors d'une release notable, en cohérence avec le tag de
  l'historique.

## Style de travail attendu

- Réponses en français (préférence utilisateur globale).
- Avant de "patcher" un comportement, vérifier que ce n'est pas déjà couvert
  par une décision dans `HANDOFF.md` § "Décisions clés" — éviter les
  régressions sur ce qui a déjà été délibéré.
- Pour toute modification non-triviale : commit + push direct quand le
  changement est clair, ou TodoWrite + plan quand c'est un chantier
  multi-fichiers.
