# Veille parlementaire Lidl — Sideline Conseil

Outil de veille institutionnelle automatisé dédié à la grande distribution alimentaire, pour le compte Lidl France :
- agrège les publications officielles (Parlement, Élysée, Matignon, ministères, JORF, autorités) ;
- filtre sur un lexique grande distribution alimentaire dédié (`config/keywords.yml`), avec distinction matching direct vs contextuel ;
- envoie un email quotidien à 08:00 Europe/Paris (= 06:00 UTC) à ANOUCK.PAUMARD@lidl.fr et publie un site statique sur `https://veille-lidl.sideline-conseil.fr`.

Exclusivement des **sources officielles publiques** — aucune collecte de réseaux sociaux ni de presse privée.

## 1. Architecture

```
veille-parlementaire-lidl/
├── config/
│   ├── sources.yml       # inventaire des sources (AN, Sénat, ministères ciblés, autorités)
│   └── keywords.yml      # lexique grande distribution (acteur | groupe | dirigeant | theme | dispositif | evenement)
│                         # chaque entrée porte un mode: direct | contextual (+ requires_any optionnel)
├── src/
│   ├── main.py           # orchestration
│   ├── normalize.py      # dispatcher vers connecteurs
│   ├── keywords.py       # matcher (direct + contextuel) + normalisation accents/casse
│   ├── store.py          # SQLite + dédup
│   ├── digest.py         # email HTML aux couleurs Lidl
│   ├── site_export.py    # JSON + Markdown pour Hugo
│   ├── models.py         # Item pivot (pydantic v2)
│   └── sources/          # connecteurs AN, Sénat, Élysée, DILA, ministères, autorités
├── site/                 # site Hugo (layouts Lidl, header co-brandé Sideline + Lidl)
├── scripts/
│   ├── audit_sources.py  # ping HEAD toutes les sources
│   └── backfill.py       # premier run sur N jours
├── tests/                # pytest
├── .github/workflows/daily.yml  # cron quotidien 06h UTC
└── pyproject.toml
```

## 2. Catégories Follaw.sv

Les 9 catégories retenues : Dossiers législatifs, JORF, Amendements, Questions,
Comptes-rendus, Publications, Nominations, Agenda, Communiqués — identiques à l'instance sport.

## 3. Périmètre & matching

Le périmètre complet est décrit dans le PDF client *Veille_Parlementaire_Lidl_Perimetre.pdf*.
Deux modes de matching cohabitent :

- **Direct** — le terme est assez spécifique pour être retenu dès qu'il apparaît
  (ex. `Lidl`, `EGalim`, `SRP+10`, `Nutri-score`, `CDAC`, `ouverture dominicale`).
- **Contextuel** — le terme n'est retenu que s'il co-apparaît dans le même
  texte avec au moins un autre mot-clé du champ (ex. `Carrefour`, `Casino`,
  `Leclerc`, `agriculture`, `franchise`, `outre-mer`).

La liste `requires_any` dans `config/keywords.yml` permet de resserrer le
contexte quand utile (`Carrefour` exige par exemple `groupe / enseigne /
hypermarché / Bompard / grande distribution / EGalim / SRP+10`).

## 4. Mise en production — checklist

### 4.1. Repo GitHub

Repo privé : `sideline-conseil/veille-parlementaire-lidl`.

### 4.2. DNS — sous-domaine veille-lidl.sideline-conseil.fr

Chez le registrar :
```
veille-lidl.sideline-conseil.fr  →  sideline-conseil.github.io  (CNAME)
```
Le fichier `site/static/CNAME` est mis à jour au passage.

### 4.3. Secrets GitHub Actions

Dans `Settings ▸ Secrets and variables ▸ Actions`, créer les 6 secrets :

| Secret              | Valeur                                                  |
|---------------------|---------------------------------------------------------|
| `SMTP_HOST`         | `ssl0.ovh.net` (réutilisé du compte Sideline sport)     |
| `SMTP_PORT`         | `587`                                                   |
| `SMTP_USER`         | `veille@sideline-conseil.fr`                            |
| `SMTP_PASS`         | mot de passe SMTP OVH                                   |
| `SMTP_FROM`         | `Sideline Veille Lidl <veille@sideline-conseil.fr>`     |
| `DIGEST_TO`         | `ANOUCK.PAUMARD@lidl.fr`                                |

### 4.4. Activer GitHub Pages

`Settings ▸ Pages ▸ Source = GitHub Actions`.

### 4.5. Premier run — backfill 7 jours

`Actions ▸ Veille parlementaire Lidl — daily ▸ Run workflow`, saisir `since_days=7`.

## 5. Utilisation quotidienne

- Email à 08:00 (heure d'été) / 07:00 (heure d'hiver) — contrainte GitHub Actions qui
  ne connaît que l'UTC. Cron unique `0 6 * * *` (06h UTC).
- Site consultable : https://veille-lidl.sideline-conseil.fr.
- Replay manuel possible à tout moment via `Actions ▸ Run workflow`.
- Historique SQLite persisté via GitHub cache.

## 6. Maintenance

- **Audit mensuel des sources** : `python scripts/audit_sources.py`.
- **Ajout d'un mot-clé** : éditer `config/keywords.yml` (aucun redéploiement de code).
  Préciser `mode: direct` ou `mode: contextual` selon le risque de bruit.
- **Ajout d'une source HTML** : entrée dans `config/sources.yml` avec `format: html`.

## 7. Développement

```bash
pip install -e ".[dev]"
python -m pytest tests/ -v
python -m src.main dry -v          # fetch + match, sans écriture
python -m src.main run --since 7 --no-email -v
```

## 8. Conformité

- **Sources** : uniquement des portails publics officiels.
- **User-Agent déclaratif** : `SidelineVeilleBot/0.1 Lidl (+https://veille-lidl.sideline-conseil.fr)`.
- **Politesse réseau** : backoff exponentiel (tenacity), parallélisme limité.
- **Aucune donnée personnelle collectée** — l'outil ne publie que des contenus déjà publics.

## 9. Contacts

- Éditeur : **Sideline Conseil** — cyrilmourin@sideline-conseil.fr
- Destinataire client : ANOUCK.PAUMARD@lidl.fr
