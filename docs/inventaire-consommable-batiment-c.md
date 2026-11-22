# Inventaire consommable bâtiment C — Documentation

> Fork de l'**inventaire de cibles** (`inventaire_cibles/`), adapté aux **consommables de
> laboratoire du bâtiment C** (EPI, verrerie, seringues, emballages, adhésifs…).
> Le socle serveur est réutilisé tel quel (FastAPI + SQLite + FTS5 + UI) ; la couche
> « 1 ligne = 1 objet physique » a été remplacée par une gestion de **quantités**.

## Vue d'ensemble

| | |
|---|---|
| **URL** | <http://mn-panda/consommable_batiment_c/> |
| **Dépôt** | `04_picture_to_CAS/consommable_batiment_c/` |
| **Service** | `InventoryBatimentC` (NSSM, port 8003) |
| **Base** | `server_display/consommable_batiment_c.sqlite` |
| **Alerte** | tâche `BatimentCLowStockAlert`, quotidienne 08:00 |

- **1 ligne = 1 article** portant une quantité — 87 articles importés depuis
  `Inventaire consomato.xlsx`, onglet `inventaire Mezzanine`.
- **Source de vérité = l'application web** après l'import ; l'import est one-shot destructif.
- Édition **ouverte** (pas d'authentification), seulement limitée par rate-limit ; lecture
  protégeable par mot de passe optionnel. À revoir en cas d'exposition hors LAN.

> **Nommage.** L'*identité* de l'application est `consommable_batiment_c` : dossier, service,
> URL, tuile, base, tâche. Le *domaine* reste « consommables », d'où les noms de modules
> conservés tels quels — `app/consommables_core.py`, `static/consommables.css`,
> `scripts/import_consommables_xlsx.py`. Ils décrivent ce que le code manipule, pas quelle
> instance le fait tourner.

### Ce qui change par rapport aux cibles

|  | Cibles | Consommables |
|---|---|---|
| 1 ligne = | 1 objet physique | 1 **article** portant une quantité |
| Stock = | `COUNT(*)` des lignes non-`Finie` | le champ `quantite` |
| Seuil = | par catégorie `matériau\|forme\|dimension` | par **article** (`seuil_min`) |
| État | Neuve/Utilisée/Finie | ✗ sans objet |
| Traçabilité | `edit_log` | `edit_log` + **journal `stock_movement`** |

Ont donc disparu : `usure`, `forme`, dimensions dérivées, page Catégories, `next-reference`,
`emplacement` dérivé de la référence, liage SDS/COA, et tout le reliquat chimie
(`find_sds-master/`, PubChem, `validate_cas.py`, parsing PDF).

## Scripts

| Script | Rôle |
|--------|------|
| `server_display/scripts/import_consommables_xlsx.py` | Import du xlsx → SQLite (mapping par position, déduction unité/catégorie/fournisseur, codes `CONS-NNN`, FTS + triggers, mouvement d'ouverture par article). One-shot destructif. Affiche un **rapport d'import**. |
| `server_display/scripts/send_low_stock_email.py` | Digest quotidien : e-mail les articles dont `quantite < seuil_min`. Réutilise `app.consommables_core.low_stock_rows`. Lecture seule ; SMTP via variables d'env. |
| `server_display/send_alert.bat` | Wrapper de la tâche planifiée : pose les variables SMTP non secrètes puis appelle le script ci-dessus. Listé dans `server_display/.gitignore` (le dossier n'est pas un dépôt git aujourd'hui, mais le secret ne doit jamais y entrer). |
| `server_display/run.py` | Point d'entrée du serveur (uvicorn). |

Côté infrastructure, dans `06_unified_work/serveur_automate/deploy/` :
`Add-InventoryBatimentC-Service.ps1` (déploie), `Remove-InventoryBatimentC-Service.ps1`
(retour arrière complet), `Rename-And-Deploy-BatimentC.ps1` (renommage du dossier + déploiement).

## Schéma de la base

| Table | Rôle |
|-------|------|
| `inventory` | 1 ligne / article. `version` (verrou optimiste), `deleted_at` (soft-delete). |
| `inventory_fts` | Index FTS5 externe synchronisé par triggers. Indexé : `code, designation, reference, categorie, emplacement, fournisseur, remarque`. |
| `stock_movement` | Journal **append-only** des mouvements de stock. |
| `edit_log` | Journal d'audit des éditions (rowid, action, field_changes JSON, user_ip, timestamp). |

**Colonnes `inventory`** : `code, designation, reference, categorie, emplacement, quantite,
unite, contenu_par_unite, seuil_min, fournisseur, photo, remarque` + `version, deleted_at`.

- `code` (`CONS-001`…) est la **clé métier**, avec un index UNIQUE. La `reference`
  fournisseur est du **texte libre non contraint** : les données sources contiennent un
  doublon réel (`113-0720` sur la combinaison XL *et* la taille M) et 12 articles sans
  aucune référence. Contraindre la référence aurait imposé un nettoyage préalable pour
  aucun gain — le code, lui, ne dépend de personne.
- `quantite` et `seuil_min` sont des `INTEGER`. **`seuil_min = 0` signifie « aucune alerte »**.
- `photo` est un chemin relatif sous `media_root/` (donc `photos/…`), servi par `GET /media/{path}`.

> ⚠️ **Source unique** : le DDL et toutes les règles métier vivent dans
> `app/consommables_core.py`, importé par le script d'import, les routes et les tests.
> Le fork cibles avait laissé `emplacement_from_reference` en **trois copies** à garder
> synchronisées ; ici il n'y en a qu'une, y compris pour la fixture de test.

### Journal des mouvements

```sql
CREATE TABLE stock_movement (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_code   TEXT NOT NULL,   -- clé MÉTIER, jamais le rowid
    delta          INTEGER NOT NULL,
    quantite_apres INTEGER NOT NULL,
    motif          TEXT,            -- import | sortie | reappro | correction
    user_ip        TEXT,
    timestamp      TEXT DEFAULT (datetime('now'))
);
```

- **`quantite` fait foi** (indexée, triable, exportable) ; chaque mouvement met à jour la
  quantité **et** écrit sa ligne de journal **dans la même transaction**. L'alternative —
  recalculer `SUM(delta)` à chaque affichage — aurait alourdi tri, export et seuils sans
  rien apporter ici.
- `quantite_apres` est stocké : le journal se lit sans jointure et se vérifie tout seul.
- `article_code` et non `rowid` : les rowid sont réattribués à chaque import, la clé métier
  survit. C'est la règle qui avait sauvé les liens documents des cibles.
- **Un mouvement d'ouverture `motif='import'`** est créé par article à l'import (et un
  `correction` à la création d'un article dans l'app), pour que l'invariant
  `SUM(delta) = quantite` tienne **dès le premier jour**.
- Le journal n'est jamais modifié ni supprimé depuis l'UI : un historique réinscriptible ne
  répond à aucune question.

**Contrôle de non-dérive** — doit toujours renvoyer 0 ligne :

```sql
SELECT code FROM inventory i
 WHERE quantite != (SELECT COALESCE(SUM(delta), 0)
                      FROM stock_movement WHERE article_code = i.code);
```

> ⚠️ C'est pour cela que `PUT /api/inventory/{rowid}` **exclut `quantite`** de ses colonnes
> éditables. Un `UPDATE` direct serait le seul moyen de faire diverger les deux.

## Règles de déduction à l'import (`app/consommables_core.py`)

### Unité et conditionnement

1. `(sachet|boîte|paquet|carton|rouleau)s? de (\d+)` → unité + `contenu_par_unite`
2. sinon `paires?` → `paire`
3. sinon `rouleau` / `film plastique` → `rouleau`
4. sinon `pièce`

Résultat sur les 87 lignes : **59 pièce · 12 paire · 5 sachet · 5 rouleau · 3 boîte ·
2 carton · 1 paquet**.

> ⚠️ **Le compte explicite (`de N`) n'est pas décoratif.** Une règle naïve sur `\bboite\b`
> classe « boite **pétri** » (1000 unités !), « boite **à pizza** » et « sachet **papier** »
> comme conditionnements, alors que le mot-contenant fait partie du **nom du produit**.
> Test de référence :
> `tests/test_consommables.py::test_deduce_unite_container_word_in_product_name_stays_piece`.

### Catégorie

Règles ordonnées, la première qui matche gagne — `petri` est testé **avant** la règle
d'emballage, sinon une boîte de Pétri finirait en « Emballage ».
Catégories : EPI, Verrerie, Seringues, Adhésifs, Nettoyage, Consommables labo, Emballage,
Outillage. Aucun match → **catégorie vide**, honnête et visible dans l'UI, plutôt qu'un
mauvais rangement que personne ne rouvrira.

### Références et fournisseur

- `fournisseur = "VWR"` si la référence a la forme `NNN-NNNN`, sinon vide.
- Double référence dans une cellule (`112-5101 ---> MAPA34710091`, `825-14AM / 817-0055`) :
  la première va dans `reference`, la seconde dans `remarque`.
  > ⚠️ Le `/` n'est un séparateur **que s'il est entouré d'espaces** : `SINGNY100/7` est une
  > référence unique dont le `/7` est une taille de gant. La première version de la règle
  > coupait ces 4 lignes et fabriquait un faux doublon.

## Endpoints API

| Méthode / route | Rôle |
|-----------------|------|
| `GET /api/search?q&sort&dir&low&limit&offset` | Recherche FTS + compteurs (`total`, `returned`, `total_all`, `count_sous_seuil`, `count_rupture`). `low=1` ne garde que les articles sous seuil. |
| `GET /api/columns` · `GET /api/suggestions` | Colonnes ; valeurs distinctes pour l'auto-complétion. |
| `GET /api/next-code` | Prochain code article libre (`max+1`, 3 chiffres). |
| `GET /api/suggest?q&limit` | Articles existants proposés comme **modèles** à la création (voir ci-dessous). Même moteur FTS que `/api/search` ; exclut les supprimés ; `limit` plafonné à 50. |
| `GET /api/inventory/{rowid}` · `POST` · `PUT` · `DELETE` | CRUD (rate-limit 10/min, soft-delete, verrou de version). **`PUT` refuse `quantite`.** |
| `POST /api/movements` | `{code, delta, motif}` → applique et journalise. Refuse un stock résultant négatif. |
| `POST /api/movements/set` | `{code, quantite}` → recomptage absolu, journalisé en `correction`. |
| `GET /api/movements?code&motif&limit&offset` | Journal paginé, plus récent d'abord. |
| `POST /api/upload-photo` · `GET /media/{path}` | Photo d'article (≤ 5 Mo, jpg/jpeg/png/webp/gif) ; service sécurisé par `safe_path`. |
| `GET /api/export?q&low&format=csv\|json` | Export de la sélection courante (CSV `;` avec BOM pour Excel). |
| `GET /api/metrics/*` | Métriques d'usage. |

> Les compteurs `count_sous_seuil` / `count_rupture` portent **toujours sur tout
> l'inventaire**, jamais sur la tranche filtrée : un badge « 12 sous seuil » qui compterait
> la page courante afficherait 12 sur 12 et ne voudrait plus rien dire.

### `GET /api/suggest` — sélecteur de modèle

Repris de l'inventaire produits chimiques (`04_picture_to_CAS/260304`), où la création
d'une fiche passe d'abord par le choix d'un flacon existant. Deux adaptations au modèle
consommables :

- **Pas de regroupement.** Là-bas N flacons partagent une fiche produit, d'où un
  `GROUP BY (nom, CAS)` et l'élection d'une ligne représentative (la plus riche en champs
  sécurité). Ici `1 ligne = 1 article` et `code` est UNIQUE : la réponse est une **liste
  plate** de lignes (`rows`), sans `groups` ni `count`.
- **Le doublon est un vrai danger.** Deux lignes pour le même article **scindent son stock**
  et faussent son seuil. Le sélecteur montre donc ce qui existe déjà *avant* la création, et
  offre « Ouvrir cette fiche » pour basculer sur l'article existant.

**Le picker n'a pas sa propre recherche** : il réutilise `build_match_query` + FTS,
exactement comme `/api/search`. Une première version cherchait la phrase entière en un seul
`LIKE '%…%'` — et « gants XL » ne remontait **rien**, parce qu'aucune désignation ne porte
ces deux mots côte à côte (« boite gants en latex taille **XL** »). Le FTS cherche chaque mot
séparément et replie les accents au passage (« mecanique » trouve « mécanique »).
Conséquence assumée : le picker cherche dans les **sept** colonnes indexées, pas seulement
`designation`/`reference`/`code`, et un mot court comme « M » remonte tout ce qui est à
« Mezzanine ». C'est le comportement de la barre de recherche que l'utilisateur connaît déjà,
et bm25 (`designation` ×10) place quand même le bon article en tête.

Repli sans FTS (`has_fts` faux) : `LIKE` **tokenisé** — chaque mot doit apparaître quelque
part, pas forcément dans la même colonne. Écrire ce repli en une seule chaîne `%…%`
réintroduirait exactement le bug ci-dessus ; c'est ce que verrouille
`test_every_word_must_match_somewhere_without_fts`. Jokers échappés par
`app/utils.py::escape_like`, sinon un `%` tapé au clavier renvoie tout l'inventaire.

Tri : les désignations qui *commencent* par le terme d'abord, puis bm25 (ou alphabétique
dans le repli) ; sans `q`, `rowid DESC` — les derniers articles créés sont ceux qu'on décline
en variante. Protégé par `check_read_auth` : il expose les mêmes données que `/api/search`.
Tests : `tests/test_suggest.py`.

## Interface (`server_display/static/`)

Trois pages : **Inventaire · Mouvements · Métriques**.

Toute la mise en page héritée des cibles est conservée telle quelle — les 5 bandes, la
hauteur bornée de `.table-wrap`, `table-layout: fixed`, la pagination ciblée par classes,
le mode sombre. **Les quatre pièges CSS documentés restent valables** :

1. `position: sticky` sur `th` **ne s'active jamais** si l'ancêtre `overflow: auto` n'a pas
   de hauteur bornée.
2. **Jamais de `transition` sur une propriété alimentée par une variable de thème** : elle
   se fige sur le thème précédent après un basculement.
3. `table-layout: fixed` + largeurs par colonne : sinon une cellule mal saisie élargit la
   colonne pour toutes les lignes.
4. Un élément flex en `margin: 0 auto` **ne s'étire pas** — il lui faut `width: 100%`.

Spécifique aux consommables :

- **La quantité EST le contrôle** : `[−] 33 [+]` dans la cellule (transposition directe du
  badge d'usure cliquable des cibles). Clic sur le nombre → saisie directe d'un recomptage.
  Le `[−]` est désactivé à 0 ; le serveur refuse de toute façon.
- **État du stock signalé par un mot** (`rupture`, `bas`), pas par la seule couleur — il doit
  survivre à un lecteur daltonien et à une impression noir et blanc (WCAG 1.4.1).
- **Bouton « Sous seuil »** dans la barre de contrôles : filtre **côté serveur**, sinon les
  articles bas des autres pages resteraient invisibles — exactement ceux que le filtre existe
  pour montrer.
- **Colonne Seuil** : `0` s'affiche `—`, parce qu'un « 0 » nu se lit comme un vrai seuil de zéro.
- **Photo** : vignette 34 px cliquable, upload dans le formulaire d'édition, `alt` = désignation.
- **Création en deux étapes** (bouton `+`) : étape 1 = sélecteur de modèle (combobox ARIA,
  liste + panneau détail), étape 2 = le formulaire. « Reprendre cette fiche » copie tout
  **sauf le code et la quantité** — le code identifie l'article dans le journal de mouvements
  et la quantité appartient au journal, les deux repartent de zéro. « Ouvrir cette fiche »
  bascule sur l'article existant (garde-fou anti-doublon), « Fiche vierge » est la sortie de
  secours. Détails à ne pas perdre en refactorisant : debounce 200 ms, garde
  `pickerRequestId` (une réponse en retard ne doit pas écraser une frappe plus récente),
  focus qui reste dans le champ avec `aria-activedescendant` qui suit ↑/↓, et
  `.picker-option.active .picker-option-meta` repassé en clair — `--muted` sur `--blue`
  tombe sous le seuil de contraste. CSS dans `consommables.css` et non `styles.css` : le
  picker n'existe que sur `index.html`.
- **Page Mouvements** : journal filtrable par article et par motif ; cliquer un code filtre
  dessus. Horodatage SQLite (UTC) converti en heure locale à l'affichage.
- Colonnes masquées par défaut : Code, Photo, Unité, Contenu, Fournisseur, Remarque.
  Clé `localStorage` : `columnVisibilityConsommables`.
- **Cache-busting** : `styles.css?v=38`, `consommables.css?v=2`, `theme.js?v=4`, `app.js?v=1`,
  `edit.js?v=2`, `movements-app.js?v=1`, `favicon.svg?v=1`.
  ⚠️ `styles.css` et `theme.js` sont chargés par les **trois** pages : bumper partout.

## Commandes clés

```bash
# venv (déjà créé) : server_display/venv
cd server_display && venv/Scripts/python.exe -m pip install -r requirements-server.txt pytest httpx

# Import (depuis la RACINE du dépôt — les chemins par défaut lui sont relatifs)
python server_display/scripts/import_consommables_xlsx.py \
    --xlsx "Inventaire consomato.xlsx" --sheet "inventaire Mezzanine" \
    --db server_display/consommable_batiment_c.sqlite

# Serveur de DEV (choisir un port libre : 8000/8001/8002 sont les autres inventaires,
# et 8003 est le service de production de cette app)
cd server_display && venv/Scripts/python.exe run.py --db consommable_batiment_c.sqlite --port 8042

# Tests
cd server_display && venv/Scripts/python.exe -m pytest tests/
```

## Alerte stock bas (digest e-mail quotidien)

> ✅ **En service depuis le 2026-08-10.** Tâche planifiée `BatimentCLowStockAlert` (compte
> `SYSTEM`, quotidienne 08:00), exécution réelle testée : `LastTaskResult = 0`.
> Wrapper : `server_display/send_alert.bat` (pose les variables SMTP non secrètes).

`scripts/send_low_stock_email.py` envoie **un** e-mail par jour listant les articles dont
`quantite < seuil_min`. Rien de bas → **aucun mail** (sauf `--always`). Lecture seule sur la
base, aucune dépendance ajoutée (`smtplib` stdlib) ; le serveur n'a pas besoin de tourner.
Sujet : `[Consommables Bât. C] N article(s) sous le seuil`.

**Flags** : `--db consommable_batiment_c.sqlite`, `--dry-run`, `--always`.

> ⚠️ **Sans seuil, aucune alerte ne partira jamais.** Le prédicat est strict
> (`quantite < seuil_min`) et **`seuil_min = 0` équivaut à « désactivé »** : il faut `≥ 1`.
> En cas de doute, `--always` permet de distinguer « chaîne cassée » de « rien à signaler ».

**Config SMTP — variables d'environnement** (secret **jamais** dans le code ni loggé) :
`SMTP_HOST`, `SMTP_PORT` (587), `SMTP_STARTTLS` (true), `SMTP_USER`/`SMTP_PASSWORD`
(optionnels), `ALERT_TO`, `ALERT_FROM`.

- **Microsoft 365 « direct send »** (retenu et validé pour les cibles le 2026-07-22) :
  `SMTP_HOST=materianova-be.mail.protection.outlook.com`, `SMTP_PORT=25`,
  `SMTP_STARTTLS=false`, **sans identifiants**. Aucun secret à stocker, aucun ticket IT.
  **Réserve** : l'IP du serveur n'est pas au SPF du domaine → le message peut arriver en
  Indésirables. Correctif durable : règle de flux Exchange `SCL = -1` côté IT.
- ❌ **Impasse à ne pas rouvrir** : l'envoi *authentifié* (`smtp.office365.com:587`) échoue
  avec `535 5.7.139` — SMTP AUTH est désactivé sur le tenant. Ne pas ouvrir de ticket sans
  confirmation écrite qu'il a été réactivé.

## Déploiement

Sur le modèle des cibles : service NSSM + route Nginx + tuile `services.yaml` + tâche planifiée.

| Élément | Valeur |
|---------|--------|
| Service Windows | `InventoryBatimentC` (NSSM, LocalSystem, auto-restart), port **8003** |
| Commande | `run.py --db consommable_batiment_c.sqlite --port 8003` |
| URL | `http://mn-panda/consommable_batiment_c/` (Nginx `location /consommable_batiment_c/` → `127.0.0.1:8003`) |
| Tuile dashboard | `serveur_automate/dashboard/services.yaml`, slug `consommables` |
| Tâche planifiée | `BatimentCLowStockAlert` (SYSTEM, quotidienne 08:00) |

> ⚠️ **8002 n'était pas libre** (contrôlé le 2026-08-10) : 8000 (inventaire chimique),
> 8001 (cibles) **et 8002** (inventaire chimique exp) sont tous servis sur cette machine.
> D'où **8003**. Revérifier avant tout nouveau service — `curl http://127.0.0.1:PORT/` suffit.

> **Pas de `--edit-password`.** Le fork cibles le passait (`"test123"`), mais il ne protège
> rien : aucune route ne porte de garde d'authentification en écriture. Le `check_auth` du
> socle n'était appelé nulle part — il a été retiré, avec un commentaire à l'endroit où une
> vraie garde devrait se brancher si l'app sortait du LAN.

### ✅ En service depuis le 2026-08-10

Vérifié après déploiement : service `Running` (démarrage automatique), `/consommable_batiment_c/`
répond **200** à travers Nginx, tous les assets et endpoints servis sous le préfixe, un
mouvement aller-retour journalisé avec l'IP réelle du poste, invariant
`SUM(delta) = quantite` intact, et les six autres services proxifiés (dashboard, inventaire
chimique, cibles, chimique exp, plotter, bce) toujours debout après le redémarrage de Nginx.

Pour rejouer le déploiement (idempotent), en PowerShell **élevé** :

```powershell
& 'D:\restart\00_Le_Code\06_unified_work\serveur_automate\deploy\Add-InventoryBatimentC-Service.ps1'
```

Le script est **idempotent** et enchaîne : contrôle du venv et de la base → création du
service NSSM → `nginx -t` **puis** rechargement (il refuse de recharger si le test échoue —
Nginx sert aussi le dashboard et les autres inventaires) → redémarrage du Dashboard →
tâche planifiée → vérification.

### ⏳ Reste à faire : renommer le dossier

`consommables_chips` → `consommable_batiment_c`. Tout le reste porte déjà le nouveau nom
(service, URL, tuile, tâche, base, code, doc).

Un dossier ne peut pas être renommé tant qu'un processus l'a comme **répertoire courant** —
et une session Claude Code y épingle ses shells Bash *et* PowerShell, que le harness y
ramène après chaque commande (tuer les shells ne suffit pas, ils repartent au même endroit).

En attendant, **deux replis explicites** gardent l'application en ligne :

| Fichier | Repli |
|---------|-------|
| `Add-InventoryBatimentC-Service.ps1` | si `consommable_batiment_c\server_display` est absent, pointe sur `consommables_chips\server_display` (avertissement en jaune) |
| `server_display/send_alert.bat` | même logique, testée : tâche exécutée à la main → `LastTaskResult = 0` |

**Procédure.** Fermer la session Claude Code, puis depuis un PowerShell **élevé** situé
ailleurs que dans le dossier :

```powershell
& 'D:\restart\00_Le_Code\06_unified_work\serveur_automate\deploy\Rename-And-Deploy-BatimentC.ps1'
```

Le script arrête le service, renomme, puis relance `Add-InventoryBatimentC-Service.ps1`.
Il refuse de tourner s'il est lui-même dans le dossier visé, et en cas d'échec **liste les
processus qui tiennent le verrou** plutôt que de renvoyer un message opaque.

Ensuite : rouvrir la session dans `consommable_batiment_c`, et **supprimer les deux replis**
ci-dessus — ils n'ont plus de raison d'être et masqueraient une vraie erreur de chemin.

### Deux pièges rencontrés au déploiement (2026-08-10)

**1. `nginx -t` écrit sur stderr même quand tout va bien.** PowerShell convertit alors
chaque ligne en `ErrorRecord` ; avec `$ErrorActionPreference = 'Stop'`, c'est **terminant** :
le script s'arrêtait net sur un test *réussi* (« syntax is ok »), service créé mais Nginx
jamais rechargé. Neutraliser l'EAP ne suffit pas — cela laisse un pavé rouge trompeur. La
parade retenue est le helper `Invoke-Native`, qui **fusionne les flux dans `cmd.exe`**
(`cmd /c "… 2>&1"`) : PowerShell ne reçoit que du texte, et le verdict vient de
`$LASTEXITCODE` seul. Le même piège guette tout appel d'exécutable natif bavard.

**2. `nginx -s reload` échoue en « Accès refusé ».** Le master Nginx tourne en **service
sous `NT AUTHORITY\SYSTEM`** ; le signal passe par un objet event global
(`Global\ngx_reload_<pid>`) dont la DACL n'autorise que SYSTEM. Depuis une session admin
ordinaire : `OpenEvent(...) failed (5)`. Les scripts passent donc par le SCM
(`Restart-Service Nginx`), qui s'exécute en SYSTEM.

> ⚠️ **Ce n'est pas un rechargement gracieux** : `Restart-Service Nginx` coupe ~1 seconde
> **tous** les services proxifiés (dashboard, inventaires, plotter, bce…), pas seulement le
> nouveau. Acceptable sur cet intranet, mais à faire hors coup de feu — et à savoir avant de
> lancer le script. Un vrai reload gracieux demanderait de déclencher `nginx -s reload`
> depuis un contexte SYSTEM (tâche planifiée ponctuelle, PsExec).

### Mettre à jour le code en production

Deux régimes, à ne pas confondre :

| Ce qui change | Geste |
|---------------|-------|
| `static/` (HTML, CSS, JS) | **Rien** — FastAPI relit le dossier à chaque requête. Bumper le `?v=` suffit, sinon le navigateur ressert sa copie en cache et le correctif paraît sans effet. |
| `app/` (Python : routes, `consommables_core`, `utils`) | **`Restart-Service InventoryBatimentC`** — le code est chargé au démarrage du processus. |

```powershell
Restart-Service InventoryBatimentC
Invoke-RestMethod 'http://127.0.0.1:8003/api/suggest?q=gants%20XL' | Select-Object returned
```

**Retour arrière** (service, tâche, bloc Nginx, tuile ; ne touche pas aux données) :

```powershell
& 'D:\restart\00_Le_Code\06_unified_work\serveur_automate\deploy\Remove-InventoryBatimentC-Service.ps1'
```

L'app sert ses assets en **chemins relatifs** → fonctionne telle quelle sous le préfixe
`/consommable_batiment_c/` (Nginx strippe le préfixe via le `/` final du `proxy_pass`).
Détails serveur : `docs/server-operation.md` et le `README.md` de
`06_unified_work/serveur_automate/`.

## Ré-import (nouvelle version du xlsx)

`import_consommables_xlsx.py` droppe `inventory` / `inventory_fts` / `edit_log`, mais
**préserve `stock_movement`** (indexé sur `article_code`, pas sur `rowid`). Ajouter
`--reset-movements` pour repartir d'un journal vide.

Ce qui **est** perdu : toutes les éditions faites dans l'app (seuils, catégories corrigées,
photos, remarques) et l'historique `edit_log`. Le xlsx redevient la source de vérité.

⚠️ Les codes `CONS-NNN` sont réattribués **par position dans le fichier**. Si l'ordre des
lignes change, le journal conservé se retrouve rattaché aux mauvais articles. Sur un
ré-import où l'ordre bouge, utiliser `--reset-movements`.

## Points laissés en défaut (KISS) / restant

### À faire

1. ⏳ **Renommer le dossier** `consommables_chips` → `consommable_batiment_c` (voir
   « Déploiement »). Tout le reste porte déjà le nouveau nom ; le service et le wrapper
   d'alerte ont un repli explicite sur l'ancien chemin en attendant. **Ces replis pourront
   sauter une fois le renommage fait** (`Add-InventoryBatimentC-Service.ps1` et
   `send_alert.bat`).
2. 🔴 **Aucun seuil n'est renseigné** (`seuil_min = 0` partout) : la tâche de 08:00 tourne
   et ne signale jamais rien. C'est le geste qui rend l'outil utile. Candidats immédiats :
   les 3 articles en rupture (Pinces Zinc, Stylo diamant, gants anti-coupures taille 11).
3. **Revue d'import** non tranchée : le `800 sachet de 100` de `CONS-025`, et les 11 lignes
   à promouvoir en boîte/sachet (gants latex et nitrile surtout).

### Choix assumés

- **Auteur d'un mouvement = IP + horodatage**, pas de nom (choix assumé : aucune
  authentification dans le socle).
- **Le sélecteur de modèle cherche dans les sept colonnes FTS**, pas seulement dans les trois
  qui identifient un article : un mot court y remonte large (« M » attrape « Mezzanine »).
  Assumé pour n'avoir **qu'une** sémantique de recherche dans l'app — voir
  `GET /api/suggest`. Le restreindre reviendrait à créer une exception que l'utilisateur
  devrait apprendre.
- **« Reprendre cette fiche » recopie la désignation et la référence.** Le doublon reste donc
  possible en deux clics ; ce qui le prévient est le bouton « Ouvrir cette fiche » et le fait
  de voir la liste avant de créer, pas un champ vidé de force. À revoir si des doublons
  apparaissent réellement dans le journal.
- `contenu_par_unite` gardé en texte (il vient d'une regex sur la désignation).
- Attributs noyés dans la désignation (taille S/M/L/XL, volume, dimensions) **non extraits** :
  4 lignes de gants latex, 4 de nitrile, 4 de combinaisons restent des articles distincts.
  Correct fonctionnellement — chaque taille se commande séparément.
- 11 articles où le conditionnement est probable mais non chiffré sont importés en `pièce`
  (voir le rapport d'import) — à promouvoir à la main dans l'app.
- **Authentification en écriture** non implémentée (OK LAN, à revoir si exposition réseau).
