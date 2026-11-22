# Inventaire Cibles — Documentation

> Fork de l'outil d'inventaire chimique, adapté à un **inventaire de cibles métalliques**
> (cibles de dépôt / sputtering). Réutilise le socle serveur (FastAPI + SQLite + FTS5 + UI),
> la couche chimie (CAS, PubChem, GHS, ICPE, SDS chimie) a été retirée.

## Vue d'ensemble

- **1 ligne = 1 cible physique** (~1078 items depuis la refonte du 2026-07-13), importées depuis
  `Listing toutes cibles.xlsx` (onglet `test`).
- **Source de vérité = l'application web** après l'import ; l'import est one-shot destructif. Un
  ré-import (nouvelle version du xlsx) **écrase toutes les éditions faites dans l'app** (usure,
  remarques, emplacements) — procédure et garde-fous : voir « Ré-import ».
- Serveur FastAPI dans `server_display/`, base `server_display/inventory.sqlite`.
- Édition **ouverte** (pas d'authentification), seulement limitée par rate-limit ; lecture protégeable
  par mot de passe optionnel. À revoir si exposition hors LAN.

## Scripts

| Script | Rôle |
|--------|------|
| `server_display/scripts/import_cibles_xlsx.py` | Import du xlsx → SQLite (mapping par position, normalisation `usure`/`forme`, consolidation des notes en `remarque`, `emplacement` dérivé de la référence, FTS + triggers). One-shot destructif. |
| `server_display/scripts/link_documents.py` | Liage automatique des **SDS** (par matériau) et **certificats COA** (par référence) → tables `material_sds` / `coa_link`. Rejouable ; préserve les liens `source='manual'`. |
| `server_display/scripts/send_low_stock_email.py` | Digest quotidien : e-mail les catégories dont le stock dispo est **sous le seuil** (`category_thresholds`). Réutilise `app.categories_core.aggregate_categories`. Lecture seule ; SMTP via variables d'env. Voir « Alerte stock bas ». |
| `server_display/scripts/migrate_csv_to_sqlite.py` | (Hérité) migration CSV→SQLite, gabarit de l'import xlsx. |
| `server_display/run.py` | Point d'entrée du serveur (uvicorn). |

## Schéma de la base

| Table | Rôle |
|-------|------|
| `inventory` | 1 ligne / cible. Colonnes ci-dessous. `version` (verrou optimiste), `deleted_at` (soft-delete). |
| `inventory_fts` | Index FTS5 externe (contentless) synchronisé par triggers. Colonnes indexées : `materiau, reference, forme, emplacement, fournisseur, remarque`. |
| `edit_log` | Journal d'audit (rowid, action, field_changes JSON, user_ip, timestamp). |
| `category_thresholds` | Seuil de stock min par catégorie `materiau|forme|dimension` (clé à 3 segments). |
| `material_sds` | Liens SDS **par matériau** (1→N). `(materiau, path, source)`. |
| `coa_link` | Liens certificats **par référence** (N↔N). `(reference, path, source)`. |

**Colonnes `inventory`** (noms internes, libellés FR côté UI) :
`materiau, reference, date_achat, prix, purete, equipement, usure, particularite,
fournisseur, hauteur_mm, forme, longueur, largeur, hauteur, diametre, epaisseur,
bon_commande, emplacement, remarque` + `version, deleted_at`.

- `usure` ∈ {`Neuve`, `Utilisée`, `Finie`} (normalisé). `forme` ∈ {`Circulaire`, `Rectangulaire`, `Cylindrique`, ""}.
- `prix` (mélange €/$), `epaisseur` (`6mm`, `1/4inch`…) gardés bruts. `hauteur_mm`/`hauteur` = doublon assumé.
- `source` des liens documents : `auto`, `auto_element` (repli alliage), `auto_ref`, `auto_range`, `manual`.
- `emplacement` est **dérivé de la référence** : token juste avant le n° final
  (`Ag-TM2-ABBE-005` → `ABBE`, `Au-TM2-SUN-001` → `SUN`). Une référence sans n° final donne `""`.
  **Cas particulier — famille AGC** : elle se replie toujours sur `AGC`, quel que soit le sous-code
  (`Ag-P.AGC_P_002` et `Ag-P.AGC_G_002` → `AGC`, et non `P`/`G`).

> ⚠️ La règle `emplacement` existe en **3 copies à garder synchronisées** :
> `app/utils.py::emplacement_from_reference` (API create/update), `scripts/import_cibles_xlsx.py`
> (même fonction, à l'import) et `static/edit.js::emplacementFromRef` (remplissage live du
> formulaire). Modifier l'une sans les autres crée des emplacements incohérents selon le chemin
> d'écriture. Test de référence : `tests/test_cibles.py::test_emplacement_from_reference`.

## Recherche intelligente

Parseur dans `app/utils.py::build_match_query` (+ `load_search_vocab`) :

- Chaque mot est comparé aux **vocabulaires contrôlés** (matériaux, formes lus de la base) :
  matériau connu → `materiau:"X"` (exact), forme connue → `forme:"Y"` (exact), sinon terme libre
  multi-colonnes avec préfixe (`"mot"*`).
- Insensible aux accents ; abréviations `rect`/`circ`/`cyl`.
- Classement **bm25 pondéré** : `materiau`×10, `forme`×5 > `reference`/`fournisseur`/`remarque`.
- Exemple : `Ag rectangulaire` → `materiau:"Ag" AND forme:"Rectangulaire"` (corrige la fuite du token
  `AGC` de la colonne `reference` : 108 → 11 résultats).

Le même parseur est réutilisé côté page **Catégories** (filtre client multi-termes ET) et pour l'export.

## Endpoints API

| Méthode / route | Rôle |
|-----------------|------|
| `GET /api/search?q&sort&dir&limit&offset` | Recherche FTS + compteurs (`total`, `returned`, `count_neuve`, `count_finie`) ; pagination via `limit`/`offset` (l'UI navigue page par page) ; chaque ligne enrichie de `sds_docs` / `coa_docs`. |
| `GET /api/columns` | Liste des colonnes. |
| `GET /api/inventory/{rowid}` · `POST /api/inventory` · `PUT /api/inventory/{rowid}` · `DELETE /api/inventory/{rowid}` | CRUD (rate-limit 10/min, soft-delete, verrou de version, garde d'unicité de référence à la création). |
| `POST /api/inventory/{rowid}/advance-usure` | Fait cycler l'état Neuve→Utilisée→Finie→… |
| `GET /api/categories` · `PUT /api/categories/{key}` | Vue stock par `materiau|forme|dimension` (champs `dimension`, `count`, `threshold`, flag `below`) ; fixer/effacer un seuil. Le `count` est le **stock disponible** : les cibles `Finie` en sont exclues. Clé dérivée : dimension = `Ø{diamètre}` (circulaire) ou `{longueur}×{largeur}` (rectangulaire). |
| `GET /api/suggestions` | Valeurs distinctes (materiau/forme/fournisseur/purete) pour l'auto-complétion. |
| `GET /api/next-reference?prefix=` | Prochain n° de référence pour une famille (max+1, 3 chiffres). |
| `GET /api/sds-files` · `GET /api/coa-files` | Fichiers disponibles pour le liage manuel. |
| `POST/DELETE /api/material-sds` | Lier/délier une SDS à un **matériau** (partagé par toutes ses cibles). |
| `POST/DELETE /api/coa-link` | Lier/délier un certificat à une **référence**. |
| `GET /sds/{path}` | Sert un document (sécurisé par `safe_path`, racine `sds_root=server_display/`). |
| `GET /api/export` · `GET /api/metrics/*` | Export (référence/matériau/forme) et métriques d'usage. |

## Interface (server_display/static/)

- **Mise en page pleine hauteur** (`body.app-screen`) : la page inventaire tient **dans un seul
  écran**, seul le tableau défile. `html`/`body` en `height: 100%; overflow: hidden`, `.shell` en
  colonne flex, et `.table-wrap` en `flex: 1; min-height: 0` — il absorbe donc automatiquement
  l'espace restant, sans calcul de hauteur à maintenir.
  ⚠️ La classe `app-screen` est portée **par `index.html` seulement** : `styles.css` est partagé avec
  `categories.html` et `metrics.html`, qui doivent garder un défilement de page normal.
  ⚠️ **Le tableau doit garder une hauteur bornée** (ici par le flex, sinon par `max-height`). Sans
  contrainte, ce conteneur — qui porte `overflow: auto` — s'étire sur toute la liste (~6700 px), avec
  deux conséquences non évidentes : la **barre de défilement horizontale** part hors de portée tout en
  bas, et surtout le `position: sticky` des `th` **ne s'active jamais** (son scrollport est ce
  conteneur, qui ne défile alors pas verticalement). Les `th` ont `z-index: 2` et un fond opaque.
  Un `@media (max-height: 520px)` rend le défilement normal sur écran court ou zoom fort (WCAG 1.4.4),
  plutôt que de rogner le contenu.
- **Largeurs de colonnes figées** (`#table { table-layout: fixed }` + `th[data-col="…"] { width }`).
  ⚠️ **C'est ce qui découple la largeur du tableau de la qualité des données.** En largeur automatique
  avec `white-space: nowrap`, la cellule la plus large d'une colonne impose sa largeur aux 200 lignes :
  22 lignes ayant un bon de commande saisi dans `epaisseur` gonflaient cette colonne de ~40 à **314 px**
  (contenu médian : 3 caractères), et le tableau débordait de **572 px**. Les valeurs trop longues sont
  désormais tronquées (`text-overflow: ellipsis`) et restent lisibles au survol — `renderRows` pose
  `td.title`. `renderHeader` fournit déjà `th.dataset.col`, donc **tout se règle en CSS**.
- **Densité** : `th, td { padding: 6px 8px }` → ligne de **39 px** (contre 51 à 12 px de padding),
  soit ~12 lignes visibles au lieu de 7. Deux valeurs, le meilleur rapport effort/résultat du lot.
- **Colonne Documents compacte** : `docLinks()` affiche **un seul badge par type** (`SDS ×9`) au lieu
  d'un badge par fichier. Certains matériaux ont 9 SDS : en badges individuels la cellule faisait
  444 px, et une fois la colonne bornée elle débordait **en hauteur** (ligne à 85 px). `.docs-cell`
  est en `flex-wrap: nowrap` pour cette raison — un retour à la ligne y grandirait *toutes* les lignes.
- **Séparation des responsabilités — cinq bandes**, une responsabilité chacune, à position stable :

  | Bande | Responsabilité | Contenu | Hauteur |
  |-------|----------------|---------|---------|
  | ① `.site-header` | identité + **navigation applicative** | logo · Inventaire/Catégories/Métriques · 🌙 | 52 px |
  | ② `.controls` | **agir** sur les données | recherche (gauche) · `+`/Exporter (droite) | 43 px |
  | ③ `.columns-bar` | **configurer** l'affichage | libellé « Colonnes » + les 14 bascules | 32 px |
  | ④ `.table-wrap` | **données + tri** | tableau ; le tri vit dans les en-têtes | `flex: 1` |
  | ⑤ `.statusbar` | **se situer + naviguer** | compteurs (gauche) · pagination (droite) | 30 px |

  Deux « navigations » à ne pas confondre : ① change de **page**, ⑤ change de **portion de données**.
  La pagination est sous le tableau — là où l'œil la cherche, et visible puisque la page ne défile pas.
  Les écarts intra-groupe (8 px) sont plus serrés que les écarts inter-groupes (24 px) : sans cette
  différence, aucun regroupement n'est perceptible, quel que soit l'ordre des éléments.
  Les **compteurs sont en texte discret**, pas en pastilles vert saturé : la couleur doit rester
  porteuse de sens (elle est réservée aux badges d'état), et trois pastilles vives captaient
  l'attention au détriment des données.
  Coût de la séparation : **nul** — la compaction du bandeau (66 → 52 px) et la suppression d'une
  largeur inline de 80 px sur la colonne d'édition l'ont intégralement compensée (12 lignes avant
  comme après, à 720 px de haut).
- **Mobile / Android** (`@media (max-width: 700px)`). Trois pièges rencontrés, tous non évidents :
  1. ⚠️ **`min-width: 0` ET `width: 100%` sur `.shell`.** `.shell` porte `margin: 0 auto` ; or des
     **marges auto sur l'axe transversal désactivent l'étirement flex**, donc l'élément se
     dimensionnait sur son contenu (1225 px, imposé par le tableau) au lieu des 412 px de l'écran.
     Combiné à `overflow: hidden` sur `body`, **813 px de tableau étaient coupés et inaccessibles** —
     pas même atteignables par défilement. `min-width: 0` seul ne suffit pas : il faut la largeur
     explicite.
  2. **Tout ce qui est une liste passe en `flex-wrap: nowrap; overflow-x: auto`** (bascules de
     colonnes, barre de statut) : une ligne qui défile au lieu de 4 lignes empilées (146 → 32 px).
     Le sélecteur doit être `#toggle-row` — `#toggle-row { flex-wrap: wrap }` dans `cibles.css`
     (spécificité 1,0,0) bat toute combinaison de classes.
  3. **`flex-direction: row` à réaffirmer sur `.site-header`** : la règle héritée
     `@media (max-width: 900px) { .site-header { flex-direction: column } }` empilait logo,
     navigation et bouton de thème sur 3 lignes (126 px au lieu de 50).

  Résultat sur 412×915 : décor **55 % → 24 %** de l'écran, tableau 376 → 659 px, **7 → 13 lignes**,
  0 px inaccessible. Le tableau garde ses largeurs fixes et défile horizontalement dans son panneau.
  `height: 100dvh` (avec repli `100%`) suit la barre d'adresse mobile qui se masque au défilement.
- **Affordance de tri** : seuls les en-têtes réellement triables portent `cursor: pointer`, une
  infobulle « Trier par … » et un chevron au survol ; les colonnes synthétiques (`documents`,
  `dimensions`) sont en `not-sortable`. La colonne triée expose `aria-sort`.
- **Valeurs numériques** (`prix`, `purete`, `epaisseur`) alignées à droite en chiffres tabulaires —
  `renderRows` pose `td.dataset.col` pour permettre ce ciblage CSS par colonne.
- **Raccourci `/`** : place le focus dans la recherche depuis n'importe où ; `Échap` la vide.
- **Budget vertical** (écran 720 px) : bandeau 66 · contrôles 43 · boutons de colonnes 32 ·
  **tableau ~536 (flex)**. Titre, paragraphe d'accueil et pied de page ont été retirés au profit du
  tableau. Résultat : **0 px de débordement horizontal, 12 lignes visibles**.
- ⚠️ **`th, td` est une règle globale** partagée avec `categories.html`/`metrics.html`. Les largeurs
  fixes sont scopées à `#table` (id présent sur la seule page inventaire) ; vérifier ces deux pages
  après toute modification de `th, td`.
- **Piège : pas de `transition` sur `th { background }`.** Le fond vient de `var(--bg-2)`, qui change
  au basculement clair/sombre. Avec une transition, le fond restait figé sur le thème précédent
  jusqu'au rechargement → texte clair sur fond clair (contraste mesuré 1.09). Idem, `th:hover` doit
  utiliser une variable (`var(--bg-1)`) et non une couleur en dur.
- **Colonnes** : toutes cochables/décochables (boutons générés dynamiquement par `app.js`, état persisté
  dans `localStorage` clé `columnVisibilityCibles`). Masquées par défaut : Prix, Date, Équipement,
  Dimensions, Remarque.
- **État (usure)** : le badge coloré **est** le bouton — clic = cycle Neuve→Utilisée→Finie→…
- **Remarque** : clic sur la cellule → édition en place (Entrée = enregistrer, Échap = annuler).
- **Pagination** : **une seule** barre, intégrée à la ligne de contrôles en haut (`◀ · Page X/N · ▶`) +
  sélecteur *par page* (100/200/500). En écran fixe elle est toujours visible ; la dupliquer
  coûterait 30 px pris sur le tableau. Les contrôles sont ciblés par **classe** (`.pager-prev`,
  `.pager-next`, `.pager-info`, `.pager-size-select`) et **jamais par `id`** — `app.js` lie le même
  gestionnaire à **toutes** les occurrences via `querySelectorAll` et synchronise les sélecteurs :
  rajouter une barre en haut ne demanderait donc que du HTML, sans toucher au JS.
  Gérée via `state.offset`/`state.limit` ; l'offset est remis à 0 sur toute recherche, tri ou
  changement de taille de page. La ligne de stats affiche la tranche courante
  (ex. `201 – 400 / 1078 cibles`). Changer de page **remonte à la barre de recherche**
  (`app.js::scrollToSearch`) : sans ça on resterait en bas, là où sont les boutons. Trois garde-fous :
  la hauteur de `.site-header` (sticky) est **mesurée au moment du scroll** et soustraite, sinon la
  barre atterrirait masquée derrière ; le défilement ne se fait **jamais vers le bas** (sans quoi la
  barre du haut ferait descendre l'utilisateur déjà en haut de page) ; `prefers-reduced-motion` est
  respecté (saut instantané).
- **Documents** : colonne avec badges **SDS n** (bleu) / **COA** (vert), texte blanc ; liens `auto`
  soulignés en pointillé. Bouton **＋** → modal « Documents » (`coa.js`, `window.DocsModal`) pour voir /
  lier / délier SDS (par matériau) et COA (par référence).
- **Ajout / édition** (`edit.js`) : auto-complétion via `<datalist>`, bouton « ⟳ N° suivant ».
- **Page Catégories** (`categories.html`) : stock par **matériau + forme + dimension** (colonne
  Dimension), recherche (matériau/forme/dimension), seuils éditables par combinaison, toggle
  « sous seuil » (indicateur cliquable). Le stock affiché **exclut les cibles `Finie`** : une cible
  passée en Finie est décomptée de sa catégorie.
- **Mode sombre** : bouton 🌙 dans l'en-tête des 3 pages (`theme.js`), thème persistant, appliqué avant
  rendu (surcharge `[data-theme="dark"]` des variables de `styles.css`).
- **Favicon** (`static/favicon.svg`) : **cible** (bullseye) vert olive/blanc à mouche charbon — remplace
  le losange de danger GHS hérité de la chimie. Référencé `favicon.svg?v=4` dans `index.html`,
  `categories.html`, `metrics.html` (bumper le `?v=` à chaque changement pour casser le cache).
- **Cache-busting des assets** : JS et CSS sont référencés versionnés (état au 2026-07-22 :
  `app.js?v=31`, `edit.js?v=8`, `coa.js?v=2`, `theme.js?v=2`, `styles.css?v=36`, `cibles.css?v=10`).
  ⚠️ `styles.css`, `cibles.css` et `theme.js` sont chargés par les **trois** pages
  (`index`/`categories`/`metrics`) : bumper partout, sinon une page sert l'ancienne copie.
  **Toute modification d'un de ces fichiers impose de bumper
  son `?v=`** dans les pages qui le chargent, sinon les navigateurs servent l'ancienne copie et le
  correctif reste invisible. Aucun redémarrage de service n'est nécessaire (FastAPI relit `static/`
  à chaque requête) : un simple rechargement de page suffit.

## Liage des documents SDS / COA

Dossiers sous `server_display/` (requis pour un service sécurisé) : `SDS_cibles/`, `COA_cibles/`.

- **SDS = par matériau** (une fiche couvre toute une substance) → table `material_sds`. Matching via table
  d'alias multilingue + composés ; **repli par élément** pour les alliages sans SDS dédiée
  (`Zr Cr` → SDS Zr + SDS Cr). Couverture après refonte : **300 liens, 98 %** des lignes
  (98/107 matériaux).
- **COA = par référence** (par lot/pièce, souvent multi-matériaux) → table `coa_link`. Auto uniquement
  sur référence exacte (`auto_ref`) ou plage expansée (`auto_range`) → **64 liens, ~5 %** des lignes à
  haute confiance ; le reste est **manuel** (nommage par matériau+date non rattachable automatiquement).
- Résolution par ligne : `app/routes/documents.py::attach_documents` (appelé par `search.py`).
- Régénérer après ajout de fichiers/références : `python server_display/scripts/link_documents.py`
  (les liens `manual` sont conservés).

> ⚠️ **Lancer `link_documents.py` depuis la racine du dépôt**, pas depuis `server_display/`. Ses
> défauts (`--sds-dir server_display/SDS_cibles`, `--coa-dir server_display/COA_cibles`,
> `--db server_display/inventory.sqlite`) sont relatifs à la racine. Lancé depuis `server_display/`,
> il ne trouve **aucun fichier** et, comme il commence par `DELETE … WHERE source != 'manual'`,
> il **efface tous les liens auto** sans rien réinsérer (il affiche alors `0 links` / `from 0 files`).
> Sans gravité — relancer depuis la racine régénère tout — mais déroutant.

## Commandes clés

```bash
# venv (déjà créé) : server_display/venv
cd server_display && venv/Scripts/python.exe -m pip install -r requirements-server.txt pytest httpx

# --- Les 2 commandes suivantes se lancent DEPUIS LA RACINE du dépôt (chemins relatifs à elle) ---

# Import (one-shot destructif : DROP inventory/inventory_fts/edit_log)
python server_display/scripts/import_cibles_xlsx.py \
    --xlsx "Listing toutes cibles.xlsx" --sheet test --db server_display/inventory.sqlite

# Liage des documents (rejouable ; préserve les liens 'manual')
python server_display/scripts/link_documents.py

# Serveur (choisir un port libre ; 8000 peut être pris)
cd server_display && venv/Scripts/python.exe run.py --db inventory.sqlite --port 8042

# Tests
cd server_display && venv/Scripts/python.exe -m pytest tests/
```

## Déploiement (dashboard MATERIA)

Intégré au dashboard `http://mn-panda/dashboard/` selon le **même principe que l'inventaire
chimique** : service NSSM + tuile `services.yaml` + route Nginx statique.

| Élément | Valeur |
|---------|--------|
| **Service Windows** | `InventoryCibles` (NSSM, LocalSystem, auto-restart), port **8001** |
| **Commande** | `run.py --db inventory.sqlite --port 8001 --edit-password "test123"` |
| **URL** | `http://mn-panda/cibles/` (Nginx `/cibles/` → `127.0.0.1:8001`, proxy direct) |
| **Tuile dashboard** | `services.yaml` : `name: Inventaire de cibles`, `slug: cibles`, `url: /cibles/` |
| **Nginx** | bloc `location /cibles/` dans `C:\nginx\conf\services.conf` (`client_max_body_size 100m` pour les PDF) |

Les fichiers de déploiement vivent dans `06_unified_work/serveur_automate/` :

- `deploy/Add-InventoryCibles-Service.ps1` — crée/démarre le service (PowerShell **élevé**), puis
  relance le Dashboard (recharge `services.yaml`, mis en `lru_cache`).
- `deploy/Phase1-Install-Services.ps1` — source de vérité (entrée `InventoryCibles` intégrée) rejouée
  lors d'un redéploiement complet du stack.
- `dashboard/services.yaml` — la tuile.

L'app sert ses assets en **chemins relatifs** → fonctionne telle quelle sous le préfixe `/cibles/`
(Nginx strippe le préfixe via le `/` final du `proxy_pass`). Détails serveur : voir
`serveur_automate/README.md` (section « 2bis. InventoryCibles »).

## Alerte stock bas (digest e-mail quotidien)

> ✅ **En service depuis le 2026-07-22.** Tâche planifiée `CiblesLowStockAlert` (compte `SYSTEM`,
> quotidienne 08:00, `LastTaskResult = 0`), relais **Microsoft 365 direct send sans identifiants**,
> destinataires internes `@materianova.be`. Premier envoi réel validé
> (« 1 catégorie sous le seuil — Ag · Ø2 : 1 dispo / seuil 2 »).
>
> ⚠️ **Réserve connue** : l'IP du serveur n'est pas dans le SPF du domaine, donc le message peut
> arriver en **Indésirables**. Un correctif Outlook individuel ne protège que son auteur — pour
> couvrir tous les destinataires, demander à l'IT une **règle de flux Exchange `SCL = -1`** pour
> l'expéditeur `inventaire-cibles@materianova.be` venant de l'IP du serveur.
>
> ❌ **Impasse à ne pas rouvrir** : l'envoi *authentifié* (`smtp.office365.com:587`) échoue avec
> `535 5.7.139 Authentication unsuccessful` — SMTP AUTH est désactivé sur le tenant (Microsoft
> retire l'auth de base courant 2026). Ne pas ouvrir de ticket sans confirmation écrite qu'il a été
> réactivé. Historique complet en commentaires dans `send_alert.bat`.

Une fois les seuils renseignés (page **Catégories**), `scripts/send_low_stock_email.py` envoie
**un** e-mail récapitulatif par jour listant les catégories `Matériau · Forme · Dimension` dont le
stock disponible (hors `Finie`) est **sous le seuil**. Si rien n'est bas → **aucun mail** (sauf
`--always`). Le script est **lecture seule** sur la DB et n'ajoute **aucune dépendance** (`smtplib`
stdlib). Il lit la DB directement : le serveur n'a pas besoin de tourner.

**Flags :** `--db inventory.sqlite` (défaut), `--dry-run` (affiche le mail sans l'envoyer),
`--always` (envoie même si rien n'est bas — utile pour valider la chaîne).

> ⚠️ **Sans seuil, aucune alerte ne partira jamais.** Le prédicat est strict (`count < min_count`,
> cf. `app/categories_core.py`), donc un seuil à **`0` équivaut à « désactivé »** : il faut **`≥ 1`**.
> Et la clé de catégorie a **3 segments** (`materiau|forme|dimension`) : une clé à 2 segments héritée
> d'une ancienne version ne correspond à rien et est ignorée en silence. En cas de doute, `--always`
> permet de distinguer « chaîne cassée » de « rien à signaler ».

**Config SMTP — variables d'environnement** (secret **jamais** dans le code ni loggé) :

| Variable | Défaut | Rôle |
|----------|--------|------|
| `SMTP_HOST` | (vide) | Serveur d'envoi. Vide → le script refuse d'envoyer (utiliser `--dry-run`). |
| `SMTP_PORT` | `587` | Port SMTP. |
| `SMTP_STARTTLS` | `true` | `false` pour un relais interne sans TLS. |
| `SMTP_USER` / `SMTP_PASSWORD` | (vide) | Auth optionnelle ; si `SMTP_USER` vide → pas de login. |
| `ALERT_TO` | (destinataires internes `@materianova.be`) | Destinataire(s), séparés par virgule. |
| `ALERT_FROM` | `SMTP_USER` ou `ALERT_TO` | Expéditeur. |

- **Microsoft 365 « direct send » (retenu et validé le 2026-07-22)** :
  `SMTP_HOST=materianova-be.mail.protection.outlook.com`, `SMTP_PORT=25`, `SMTP_STARTTLS=false`,
  **sans `SMTP_USER` ni `SMTP_PASSWORD`**. Le connecteur du tenant (= le MX du domaine) accepte les
  envois non authentifiés vers des destinataires **internes** `@materianova.be`. C'est la solution
  la plus robuste ici : aucun secret à stocker, rien à renouveler, aucun ticket IT.
  **Réserve** : l'IP du serveur n'est pas dans le SPF du domaine, donc le message peut être classé en
  **Indésirables**. Si ça arrive, demander à l'IT d'ajouter l'IP au SPF (ou un connecteur dédié).
  Ne fonctionne **pas** vers une adresse externe (Gmail…) — pour ça, voir le plan B.
- **Gmail (plan B)** : `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USER=<gmail>`,
  `SMTP_PASSWORD=<mot de passe d'application>` (2FA requise). Une adresse externe passe par un relais
  authentifié : un serveur mail purement local ne livrerait pas de façon fiable (SPF/DKIM).

**Planification (Windows Task Scheduler, admin PowerShell)** — même principe que le démarrage serveur,
avec un trigger quotidien. Le wrapper `server_display/send_alert.bat` pose les variables SMTP
non-secrètes ; le mot de passe éventuel se pose au niveau machine (`setx SMTP_PASSWORD "…"`), pas dans
le fichier. `send_alert.bat` est **git-ignoré**.

```powershell
$action  = New-ScheduledTaskAction -Execute "D:\restart\00_Le_Code\04_picture_to_CAS\inventaire_cibles\server_display\send_alert.bat" -WorkingDirectory "D:\restart\00_Le_Code\04_picture_to_CAS\inventaire_cibles\server_display"
$trigger = New-ScheduledTaskTrigger -Daily -At 8:00am
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
# SYSTEM => la tache tourne meme si personne n'est connecte. Ne PAS calquer InventoryBackup,
# qui utilise un logon Interactive et ne se declenche donc que si l'utilisateur est loggue.
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount
Register-ScheduledTask -TaskName "CiblesLowStockAlert" -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description "Digest quotidien stock bas cibles"
# Tester : Start-ScheduledTask -TaskName "CiblesLowStockAlert"
#          Get-ScheduledTaskInfo -TaskName "CiblesLowStockAlert"   # LastTaskResult doit valoir 0
```

## Points laissés en défaut (KISS) / restant

- `prix` €/$ et `epaisseur` texte gardés bruts ; doublon `hauteur_mm`/`hauteur` conservé.
- Valeurs `usure` sales à l'import → défaut `Neuve`, brut préservé dans `remarque`.
- 2 SDS manquantes (téléchargements échoués : MoS2, CeO2-Gd2O3) — trou accepté.
- Délier une SDS agit sur **tout le matériau** (partagé) — indiqué dans l'UI.
- **Authentification** en écriture non implémentée (OK LAN, à revoir si exposition réseau).
- Reste inerte (chimie non retirée) : `find_sds-master/`, PubChem, `validate_cas.py`,
  `extract_sds_info`/`validate_cas_checksum` dans `utils.py` (encore importés par `files.py`).

## Ré-import (nouvelle version du xlsx)

`import_cibles_xlsx.py` fait un DROP de `inventory`/`inventory_fts`/`edit_log` mais **pas** de
`material_sds`/`coa_link`/`category_thresholds`. Les liens documents sont indexés sur des **clés
métier** (`materiau` pour les SDS, `reference` pour les COA) et jamais sur le `rowid` — or les
`rowid` sont réattribués à chaque import. **Les liaisons PDF survivent donc automatiquement**, y
compris les liens `manual`.

> ⚠️ **Règle d'or : ne jamais supprimer `server_display/inventory.sqlite`.** « Vider la base » = relancer
> l'import **en place** (il droppe lui-même `inventory`). Supprimer le fichier détruirait
> `material_sds` + `coa_link` et obligerait à refaire tout le liage manuel.

Ce qui **est** perdu : toutes les éditions faites dans l'app (usure, remarques, emplacements
corrigés) et l'historique `edit_log`. Le xlsx redevient la source de vérité.

### Procédure validée (refonte du 2026-07-13 : 866 → 1078 cibles)

Le service tient la base : l'arrêter d'abord (PowerShell **élevé**), le redémarrer à la fin.

```powershell
Stop-Service InventoryCibles      # ... procédure ci-dessous ... puis Start-Service InventoryCibles
```

```bash
cd server_display

# 0. Sauvegarde (API SQLite : gère le WAL, contrairement à une copie de fichier)
venv/Scripts/python -c "import sqlite3; sqlite3.connect('inventory.sqlite').backup(sqlite3.connect('inventory.backup.sqlite'))"

# 1. Filet : exporter les liens MANUELS avant toute opération
venv/Scripts/python -c "import sqlite3,csv; c=sqlite3.connect('inventory.sqlite'); [csv.writer(open(f'manual_{t}.csv','w',newline='',encoding='utf-8')).writerows([('key','path')]+c.execute(f'SELECT {k},path FROM {t} WHERE source=\"manual\"').fetchall()) for t,k in (('coa_link','reference'),('material_sds','materiau'))]"

cd ..   # les 2 étapes suivantes se lancent DEPUIS LA RACINE

# 2. Recharger EN PLACE (vérifier le nom de l'onglet du nouveau fichier !)
python server_display/scripts/import_cibles_xlsx.py \
    --xlsx "Listing toutes cibles.xlsx" --sheet test --db server_display/inventory.sqlite

# 3. Régénérer les liens auto (préserve les 'manual')
python server_display/scripts/link_documents.py
```

### 4. Auditer les liens manuels orphelins

Seul vrai risque d'une refonte : un lien `manual` dont la `reference`/`materiau` n'existe plus à
l'identique dans le nouveau fichier. Il reste en base (inoffensif) mais ne correspond plus à rien.

```sql
SELECT reference, path FROM coa_link
  WHERE source='manual' AND reference NOT IN (SELECT reference FROM inventory);
SELECT materiau, path FROM material_sds
  WHERE source='manual' AND materiau NOT IN (SELECT materiau FROM inventory);
```

Aucun résultat → tout s'est rerattaché, rien à refaire. Sinon, ces lignes sont **exactement** celles
à re-pointer à la main dans l'UI (le PDF physique est toujours là), ou à remapper en masse via
`UPDATE coa_link SET reference=? WHERE reference=? AND source='manual'`.

**Contrôle final** : le nombre de liens `source='manual'` doit être **inchangé** avant/après
(`SELECT source, COUNT(*) FROM coa_link GROUP BY source`).
