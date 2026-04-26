# HANDOFF — Veille parlementaire Lidl

Document vivant pour passer la main entre deux sessions de travail.
La section Historique se cumule, les autres sont réécrites à chaque passe.

---

## État actuel

### Ce qui tourne en prod

- **Repo** : `cyrilmourin/veille-parlementaire-lidl` (GitHub privé).
- **Site** : `https://veille-lidl.sideline-conseil.fr` (HTTPS via GitHub Pages, certificat Let's Encrypt, CNAME chez OVH).
- **Email quotidien** : destinataire `ANOUCK.PAUMARD@lidl.fr`, envoyé par `veille@sideline-conseil.fr` via SMTP OVH. Un seul cron, `0 6 * * *` UTC (= 08 h Paris en été, 07 h en hiver).
- **Catch-up mensuel Lidl** : `0 3 1 * *` UTC, workflow `.github/workflows/monthly_lidl_catchup.yml`. Ratisse 540 jours, pose `matched_keywords=["Lidl"]` sur tout item mentionnant littéralement la marque, sans test contextuel.
- **Version système affichée** : `Lidl-R38` + hash commit court.

### Couverture des sources

- Parlement : AN (dumps JSON opendata, législature 17), Sénat (Akoma Ntoso depots/adoptions + 3 commissions CR hebdo : affaires économiques, aménagement du territoire, affaires sociales).
- Gouvernement : Élysée, Matignon (info.gouv), MinEcon, MinAgri, MinTransitionÉcologique, MinOutre-mer.
- Autorités : Autorité de la concurrence, Cour des comptes, Conseil d'État, DGCCRF, RappelConso, OFPM, Médiateur agricole (désactivés par défaut en attendant vérif URLs), ADEME (désactivé).
- Organisations représentatives : ANIA, FNSEA, Coordination rurale, Jeunes Agriculteurs, UFC-Que Choisir (RSS officiels) + FCD, APCA, Foodwatch (HTML scraping) + Confédération paysanne (parser dédié, listing `recherche.php?type=RP`).
- JORF : DILA OPENDATA.

### Lexique

- `config/keywords.yml`, deux modes de matching :
  - **direct** : terme retenu dès apparition (ex. `Lidl`, `EGalim`, `SRP+10`, `Nutri-score`, `CDAC`, `marges de la grande distribution`).
  - **contextual** : terme retenu seulement si un des `requires_any` est présent dans le haystack (ex. `Carrefour`, `Casino`, `Leclerc`, `distributeurs`, `fournisseurs`).
- Familles : `acteur`, `groupe`, `dirigeant`, `theme_negociations`, `theme_urbanisme`, `theme_travail`, `theme_produits`, `theme_concurrence`, `theme_outremer`.

### Enrichissement du haystack

- **R35-B** : PDF des CR commissions AN extraits via pypdf → `raw.haystack_body`.
- **R38-A** : `<main>` des CR commissions Sénat strippé proprement → `raw.haystack_body`.
- **R36-E / P5** : cumul des libellés d'actes du dossier AN → `raw.libelles_haystack` (pour le dosleg lui-même, et propagé aux amendements du dossier via cache `data/an_texte_to_libelles.json`).
- **P4** : texte parlementaire AN (PDF du texte initial + rapports) extrait via pypdf → `raw.haystack_body` pour les dossiers législatifs. Cache disque `data/cache/dosleg_pdf/<uid>.txt`.

### Infra agnostique législature — PARTIEL

- **P7 (minimal, livré)** : URLs AN des dumps opendata avec placeholder `{legislature}` dans `config/sources.yml` + module `src/legislatures.py` + expansion dans `normalize.iter_sources()`.
- **Ce qui n'est PAS couvert par P7 minimal** : les `17`/`L17` hardcodés dans le code Python (audit : ~109 occurrences dans 10 fichiers — `assemblee.py`, `assemblee_dosleg_pdf.py`, `assemblee_rapports.py`, `an_cr_commissions.py`, `senat_akn.py`, `amo_loader.py`, `site_export.py`, `scripts/diag_*`). Ces URLs ne basculeront PAS automatiquement à l'ouverture de la 18e législature.
- **Décision pragmatique** (après audit croisé avec l'instance sport) : ne pas pousser P7 au-delà aujourd'hui. Le bénéfice immédiat est nul (la fenêtre courante couvre uniquement la 17e), le refactor complet coûte 2-4 h pour gagner ~30 min à la bascule. La checklist ci-dessous documente les touches manuelles à faire quand la 18e s'ouvrira.

---

## Décisions clés

### Ligne éditoriale

- **Périmètre strict** : grande distribution alimentaire. Action/Stokomani/B&M exclus. Sujets agricoles acceptés UNIQUEMENT couplés à un signal distribution (négociation commerciale, SRP+10, centrale d'achat, enseigne). *Pourquoi* : éviter le bruit massif des textes agricoles amont.
- **Cron unique 08 h Paris** plutôt que matin + après-midi (instance sport). *Pourquoi* : 1 mail/jour suffit côté Lidl, pas de besoin de temps réel.
- **Outre-mer** inclus mais strictement couplé à la grande distribution (BQP / bouclier qualité prix). *Pourquoi* : sujet à forte activité parlementaire mais très transverse — on ne garde que le volet distribution.

### Matching contextuel

- **Règle générale** : pour les termes ambigus, le `requires_any` se limite à des signaux **distinctifs** (nom d'enseigne, Bompard, E.Leclerc, Galec…). On évite les MOTS COURTS et ambigus seuls (`distributeur`, `enseigne`, `magasin`) dans les `requires_any` car, sur un long haystack JORF ou `libelles_haystack`, la simple co-occurrence distante produit du bruit.
- **P1c (2026-04-26) — expressions multi-mots distinctives en direct.** `grande distribution`, `grande distribution alimentaire`, `hard-discount`, `discount alimentaire`, `marque de distributeur` (et variantes pluriel) sont passées en mode direct. *Pourquoi* : ces chaînes relèvent du jargon précis de la GD alimentaire ; elles n'apparaissent pas dans les textes santé / éducation / défense. L'argument anti-bruit historique (« char Leclerc ») tenait au cache DB stale après resserrement du `requires_any` de Leclerc, pas à la chaîne « grande distribution » qui n'aurait pas pu causer ce match. *Décision Cyril* : EXCLUS du direct, jugés trop larges (alimentaire amont ou hors-GD possible) — `commerce alimentaire`, `commerce de détail alimentaire`, `distribution alimentaire`, `centrale d'achat` (resté contextuel). Ces 4 termes peuvent matcher via `requires_any` chez d'autres keywords.
- **EGalim n'est jamais un validateur de contexte** pour `Agriculture`, `Agriculteurs`, `Contrats amont`, `Matière première agricole`. *Pourquoi* : EGalim touche amont ET aval ; un texte « EGalim + agriculteurs » sans signal distribution n'est pas pertinent.
- **Agriculture et Agriculteurs ont été retirés du lexique**. *Pourquoi* : trop génériques sur les longs textes ; les vrais textes GD contiennent toujours un signal plus spécifique (SRP+10, centrale d'achat, nom d'enseigne).
- **P1a — formulations longues en direct** (« marges de la grande distribution », « équilibre dans les relations commerciales entre fournisseurs et distributeurs », « négociations commerciales dans la grande distribution »). *Pourquoi* : ces chaînes sont caractéristiques des titres de lois GD, quasi-jamais ailleurs.
- **P1b — Distributeurs / Fournisseurs / Relations commerciales en contextuel strict**. *Pourquoi* : fréquents dans les titres GD mais trop ambigus seuls. `requires_any` exige enseigne alimentaire ou marqueur GD distinctif (SRP+10, Descrozaille, EGalim 3, MDD, centrale d'achat).

### Infrastructure

- **P2 — URLs Sénat opendata ont silencieusement basculé** de `.csv.zip` à `.csv` direct (encodage latin-1, séparateur `;`). Fix appliqué, la page dossiers législatifs Sénat remonte à nouveau des items. *Pourquoi le noter ici* : cette migration est invisible tant qu'on n'audite pas, et reproduit le problème côté projet sport (cf. recos sport transmises).
- **P7 — URLs agnostiques législature**. *Pourquoi* : anticipe la bascule 17→18 sans qu'on ait à revenir dans la config, et permet le chevauchement pendant une période post-dissolution.

### Filtre catch-up Lidl mensuel

- **540 j + `--catchup-lidl`** : pose `matched_keywords=["Lidl"]` sur tout item mentionnant littéralement la marque, **sans test contextuel**. *Pourquoi* : la marque est sans ambiguïté ; il est inacceptable de manquer un texte qui cite Lidl explicitement, même si le reste du contenu est hors thématique habituelle.

### UI

- **Catégorie Nominations retirée**. *Pourquoi* : peu d'intérêt côté Lidl ; les rares nominations GD remonteront via la catégorie JORF standard.
- **Filtre publications** : 4 boutons (Tout, Parlement, Gouvernement, Autorités) + Organisations représentatives. Sport-specifics retirés.
- **Logos AN/Sénat en `.png`** (112×112) sur les pages dédiées dosleg et comptes rendus. Les `.svg` du dossier `logos/` sont des pictos génériques, PAS les logos officiels.
- **Revert R36-B** : le partial `chamber-badge.html` et `_fmt_item_line` rendent un cartouche texte coloré partout, sauf sur les layouts dédiés dosleg et CR qui utilisent leur logo en dur. *Pourquoi* : R36-B avait sur-étendu l'usage du logo 22×22 à toutes les listes, créant un rendu peu lisible.

### Co-branding

- **Header** : logo Lidl (détouré, fond transparent) à gauche, plus de mention « SIDELINE CONSEIL » ni de tagline « Voir clair. Jouer juste. ». Titre « Veille Institutionnelle Lidl ».
- **Palette** : bleu Lidl `#0050AA`, jaune Lidl `#FFE500` (module recherche, accents), rouge Lidl `#E60A14` (liens, CTA).
- **Favicon** : logo Lidl officiel (carré bleu + cercle jaune + texte LIDL) décliné en 16/32/180 px.

---

## TODO

### Priorité haute

- **Surveiller le cold-start P4** sur le prochain run : ~1 300 dossiers AN → 4×1 300 requêtes HTTP pour télécharger/extraire les PDF de texte initial + rapports. Timeout workflow actuel 25 min, à suivre — en cas de dépassement, réduire `MAX_TEXTES_PER_DOSSIER` / `MAX_RAPPORTS_PER_DOSSIER` ou splitter en plusieurs passes.
- **Lancer un `reset_db=1 + since_days=180`** après cold-start P4 pour que la DB soit ré-ingérée avec le haystack complet + les ajouts P1a/P1b.

### Priorité moyenne

- **Vérifier en live les URLs désactivées** (pour les passer `enabled: true`) : RappelConso (`/feed` renvoyait 404 au dernier test), DGCCRF (page 403), `min_outremer/presse`, `min_agriculture/agenda-du-ministre` (404). Remplacer par les vraies URLs quand identifiées.
- **Agendas ministres data.gouv.fr** (MinAgri, MinEco, MinCommerce) : trois sources `enabled: false` avec URLs best-effort. Vérifier l'existence du dataset.
- **OFPM et Médiateur commercial agricole** : URLs best-effort, à valider.

### Priorité basse

- **Test du catch-up Lidl mensuel** : déclenché pour la première fois le 1er mai 2026. Vérifier volume + perf. Si trop long, réduire MAX_TEXTES par dossier pendant le catchup.

---

## Pièges connus

### Cache DB vs lexique en cours d'évolution

Un `upsert_many` **ne met PAS à jour** les `matched_keywords` d'un item déjà en DB. Quand on modifie le lexique, les items historiques gardent leurs anciens matches. Pour propager proprement un changement de lexique : **`reset_db=1`** ou `reset_category=<cat>`. Piège typique : « char Leclerc » qui restait matché après le resserrement du requires_any, alors que le matcher local retournait bien `[]`.

### Faux positifs par co-occurrence distante

Sur un long texte (JORF article complet, `libelles_haystack`, `haystack_body` PDF), deux termes très éloignés sont considérés comme co-occurrents par le matcher. Conséquence : un terme contextuel validé via un `requires_any` trop COURT et générique produit du bruit. Règle : utiliser uniquement des MOTS COURTS distinctifs dans les `requires_any` (éviter `distributeur` / `enseigne` / `magasin` seuls comme validateurs).

Cas historique « char Leclerc » : RÉ-INTERPRÉTÉ en P1c (2026-04-26). Le bruit ne venait pas de l'expression « grande distribution » dans le `requires_any` de `Leclerc` — il venait du cache DB stale (l'item gardait `matched_keywords=["Leclerc"]` après que le requires_any ait été resserré, parce que `upsert_many` ne re-matche pas). Le fix réel a été `reset_db=1`. La doctrine actuelle laisse `grande distribution` en direct sans crainte.

### Concurrence hardlinks cowork ↔ git

Les fichiers du workspace du sandbox sont hardlinked ou montés sans droits d'écriture `git` complets. Astuce : faire le commit/push depuis un **clone propre** (`/tmp/vl-lidl`), en syncant via `rsync` depuis le workspace. Évite aussi les `data/veille.sqlite3.bak.*` hardlinked que le sandbox ne peut pas supprimer.

### URL AN dossier HTML protégée Cloudflare

Certaines pages `/dyn/17/dossiers/<uid>` peuvent renvoyer un challenge JS-based Cloudflare. Le fetch actuel (P4) passe via `curl_cffi` (`impersonate=True`) ce qui suffit aujourd'hui. Si un jour ça bascule en challenge avancé : fallback via un navigateur headless ou via l'opendata JSON (plus limité en contenu).

### Token PAT fine-grained et nouveaux repos

Un PAT GitHub fine-grained ne s'auto-étend pas aux repos créés après sa génération. Après création d'un nouveau repo via API, il faut **retourner dans Settings → Developer settings → Personal access tokens → éditer le token → cocher le nouveau repo**, sinon 403 au push.

### Activation GitHub Pages + custom domain

Un PAT sans scope `Pages` **ne peut pas** activer Pages ni attribuer le custom domain via API. Étapes à faire manuellement une fois : Settings → Pages → Source = GitHub Actions ; Custom domain = `veille-lidl.sideline-conseil.fr` ; Enforce HTTPS. Le certificat Let's Encrypt est émis dans les 5-15 min suivantes.

### Sous-domaine avec underscore

HTTPS / Let's Encrypt refusent les underscores dans les hostnames. Le sous-domaine initialement envisagé `veille_lidl.sideline-conseil.fr` a dû être remplacé par `veille-lidl.sideline-conseil.fr`. Pour toute nouvelle instance : **tirets seulement**.

### URLs Sénat CSV

Les URLs `/data/dosleg/*.csv.zip` renvoient 404 depuis un moment — le Sénat a silencieusement migré vers `/data/dosleg/<nom>.csv` (CSV direct, latin-1, séparateur `;`). Penser à surveiller ce type de migration invisible côté open data (même cas possible pour AN à terme).

### Slugs commissions Sénat CR

Les slugs du portail Sénat pour les CR commissions sont **courts** (`economie`, `developpement-durable`, `affaires-sociales`), pas les libellés longs (`affaires-economiques`, `amenagement-du-territoire-et-du-developpement-durable`). Ces derniers renvoient 404.

### AN CR commissions — scan et state

Sans `data/an_cr_state.json`, le scanner descendant avec `miss_tolerance=3` échoue sur les sessions en début de cycle (premier CR à n°30-40 alors qu'on scanne depuis 99). Fix : `miss_tolerance: 80` pour le cold-start initial, puis retour à ~5 quand le state est rempli.

### Bascule de législature 17 → 18 — checklist manuelle

P7 minimal couvre `config/sources.yml` et le helper `legislatures.py`. Le reste du code contient ~109 références codées en dur à la 17e législature. À l'ouverture de la 18e :

```bash
# 1) Mettre à jour le calendrier (le plus important — sans ça, le helper ne route pas)
#    Dans src/legislatures.py : ajouter Legislature(18, date(YYYY, M, D), None)
#    et passer end=date(...) sur l'entrée 17e.

# 2) Search-and-replace en dur dans le code Python (URLs et codes session)
sed -i '' 's|/dyn/17/|/dyn/18/|g' \
    src/sources/assemblee.py \
    src/sources/assemblee_dosleg_pdf.py \
    src/sources/assemblee_rapports.py \
    src/sources/an_cr_commissions.py \
    src/sources/senat_akn.py \
    src/site_export.py \
    src/amo_loader.py
sed -i '' 's|L17|L18|g' src/sources/an_cr_commissions.py src/sources/assemblee.py
sed -i '' 's|legis=17|legis=18|g' src/sources/assemblee_rapports.py

# 3) Refresh AMO cache (députés / organes 18e législature)
python scripts/refresh_amo_cache.py

# 4) Bumper SYSTEM_VERSION_LABEL (ex. Lidl-R40-legis18) dans src/site_export.py

# 5) Vider les caches indexés en dur (sinon collisions de keys PA*/PO* entre légis)
rm -f data/an_texte_to_dossier.json data/an_texte_to_libelles.json
rm -rf data/cache/dosleg_pdf

# 6) Déclencher reset_db=1 + since_days=180 pour ré-ingérer toute la 18e
gh workflow run daily.yml -f reset_db=1 -f since_days=180

# 7) Vérifier sur veille-lidl.sideline-conseil.fr que les nouveaux dossiers
#    18e remontent et que les anciens 17e ne polluent plus.
```

Coût estimé : ~30 min de bascule manuelle. Si la fréquence des bascules s'accélère (dissolutions répétées), envisager le refactor complet P7 (≈ 2-4 h) pour automatiser.

---

## Historique

- 2026-04-26 : P1c (lexique) — expressions multi-mots distinctives de la GD passées en direct : `Grande distribution`, `Grande distribution alimentaire`, `Hard-discount` / `Hard discount`, `Discount alimentaire`, `Marque de distributeur` (+ variantes pluriel). Re-cadrage de la règle anti-bruit : seuls les mots COURTS ambigus (`distributeur` / `enseigne` / `magasin`) restent à éviter en `requires_any` seul. Maintenus en contextuel sur demande Cyril : `commerce alimentaire`, `commerce de détail alimentaire`, `distribution alimentaire`, `centrale d'achat` (jugés trop larges). Comportement vérifié : `char Leclerc` toujours non-matché ; `commission d'enquête sur les marges...` continue de matcher (P1a). Penser à `reset_db=1` au prochain run pour propager P1c sur l'historique en cache.
- 2026-04-26 : Confédération paysanne — parser dédié `src/sources/confederation_paysanne.py` sur le listing `/recherche.php?type=RP&raz=1&rech=0` (HTML artisanal, pas de RSS). Format `confederation_paysanne_listing` (par paquets de 20, pagination `&dc_old=N`, dates `DD.MM.YYYY`). Source activée, 7 tests offline ajoutés. Clôture priorité basse #1 (parser Conf. paysanne) et #4 (recos sport déjà portées côté instance sport). Vérifié priorité basse #2 (commission d'enquête Sénat) : la proposition de résolution `ppr25-069` (déposée 23/10/2025, état actif) figure bien dans le dump CSV `data.senat.fr/data/dosleg/dossiers-legislatifs.csv` ; le keyword direct `Marges des industriels et de la grande distribution` (P1a) matche parfaitement le titre — elle remontera au prochain run prod.
- 2026-04-25 : P4 (haystack PDF dosleg AN) + P5 (propagation libelles aux amendements) + P7 (URLs agnostiques législature) + P2 (fix URLs Sénat CSV migrées) + P1a/P1b (lexique enrichi). Audit de couverture livré (`Audit_couverture_Veille_Lidl_v1.md`). Recos pour l'instance sport livrées (`Recos_veille_sport_issues_audit_Lidl.md`).
- 2026-04-24 : port R37 (CR commissions Sénat + scan AN + logo gouvernement) puis R38 (strip main CR Sénat + refonte page CR + anti-bruit commission). Revert R36-B (logos SVG partout) sur demande Cyril. Ajout favicon Lidl, retrait mention SIDELINE CONSEIL du header, suppression catégorie Nominations, catch-up Lidl mensuel (18 mois, sans contexte). Flux orgas représentatives audités et activés (ANIA, FNSEA, Coord. rurale, JA, UFC — RSS officiels).
- 2026-04-24 (matin) : création du repo, premier commit, activation Pages + custom domain, configuration des 6 secrets SMTP/DIGEST_TO, premier run de validation end-to-end. Livraison du PDF « Périmètre Lidl » + cahier des charges v1.0.
