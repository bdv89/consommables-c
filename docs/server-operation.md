# Server Operation

> ⚠️ **Document hérité de l'outil chimie d'origine.** Ce dépôt est le fork
> **inventaire de cibles** : les fonctions chimie (CAS, PubChem, phrases H/P, ICPE) en ont été
> retirées, et le serveur tourne sur le port 8001. Vérifier chaque affirmation avant de
> l'appliquer ici. Référence à jour : [inventaire-cibles.md](inventaire-cibles.md).


Documentation pour l'operation du serveur d'inventaire chimique.

## FastAPI Server (Recommande)

Le serveur FastAPI (`server_display/app/`) offre de meilleures performances que le serveur legacy.

### Structure

```
server_display/
├── app/
│   ├── __init__.py
│   ├── config.py          # Settings Pydantic
│   ├── dependencies.py    # get_db(), check_auth()
│   ├── main.py            # FastAPI app + lifespan
│   ├── utils.py           # safe_path, validate_cas, etc.
│   └── routes/
│       ├── export.py      # GET /api/export
│       ├── files.py       # GET /sds/*, POST /api/upload*
│       ├── icpe.py        # GET /api/icpe-summary*
│       ├── inventory.py   # CRUD /api/inventory
│       ├── metrics.py     # GET /api/metrics/*
│       └── search.py      # GET /api/search, /api/columns
├── run.py                 # Point d'entree CLI
├── requirements-server.txt
└── venv/                  # Environnement virtuel
```

### Installation

```powershell
cd server_display
python -m venv venv
.\venv\Scripts\pip install -r requirements-server.txt
```

### Lancer le serveur

```powershell
cd server_display

# Mode lecture seule
.\venv\Scripts\python run.py --db inventory.sqlite

# Mode edition (avec mot de passe)
.\venv\Scripts\python run.py --db inventory.sqlite --edit-password "test123"

# Production (4 workers)
.\venv\Scripts\python run.py --db inventory.sqlite --workers 4

# Developpement (auto-reload)
.\venv\Scripts\python run.py --db inventory.sqlite --reload
```

**Ou directement avec uvicorn:**
```powershell
cd server_display
.\venv\Scripts\uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Securite

### Decisions de deploiement (intranet mn-panda)

> **Transport : HTTP assume (pas de TLS).** L'inventaire est servi en HTTP derriere
> nginx, aligne sur la pratique IT (les autres sites intranet ne sont pas en HTTPS et
> il n'y a pas de PKI interne deployee). Si un jour le TLS devient acceptable, la voie
> propre est un certificat emis par la CA d'entreprise (AD CS), automatiquement approuve
> sur le domaine — pas un certificat auto-signe maison.

> **Lecture publique assumee.** `--read-password` est volontairement non defini : la
> recherche, les colonnes, l'export et les fichiers SDS sont accessibles **sans
> authentification** sur l'intranet.

> **Ecriture ouverte (sans mot de passe).** Par choix metier, la creation, la modification,
> la suppression de lignes et l'upload de SDS/videos se font **sans authentification** :
> n'importe quel poste de l'intranet peut ecrire. `--edit-password` n'est plus requis et
> `GET /api/edit-enabled` renvoie toujours `true`. **Risque accepte** (intranet de confiance).
> Garde-fous conserves :
> - **Rate-limit** 10 req/min/IP sur create/update/delete (anti-spam) ;
> - **Journal d'audit** (`edit_log`) : chaque ecriture est tracee avec l'IP ;
> - **Soft-delete** : les suppressions sont marquees, pas effacees ;
> - **Backups quotidiens** automatiques avant ecriture (`backups/`, rotation 7 j).
>
> Pour re-verrouiller l'edition plus tard : restaurer `Depends(check_auth)` sur les routes
> d'ecriture (`app/routes/inventory.py`, `app/routes/files.py`) et faire renvoyer
> `settings.editing_enabled` a `/api/edit-enabled`. La fonction `check_auth` est conservee
> dans `app/dependencies.py`.

### Lancement securise (recommande)

```powershell
cd server_display

# Intranet avec protection lecture + edition
.\venv\Scripts\python run.py --db inventory.sqlite \
    --read-password "lecteur123" \
    --edit-password "editeur456" \
    --rate-limit 60
```

### Protection de la lecture (--read-password)

Par defaut, l'inventaire est accessible publiquement en lecture. Pour restreindre l'acces:

```powershell
# Lecture protegee par mot de passe
.\venv\Scripts\python run.py --db inventory.sqlite --read-password "secret"
```

**Endpoints proteges:**
- `GET /api/search` - Recherche
- `GET /api/columns` - Liste des colonnes
- `GET /api/export` - Export
- `GET /sds/*` - Fichiers SDS

**Authentification:** HTTP Basic Auth (username ignore, seul le password compte)

```bash
# Sans auth -> 401 Unauthorized
curl http://localhost:8000/api/search?q=ethanol

# Avec auth -> 200 OK
curl -u ":secret" http://localhost:8000/api/search?q=ethanol
```

### Rate Limiting (--rate-limit)

Protection contre les abus et attaques par force brute.

```powershell
# 60 requetes/minute par IP (defaut)
.\venv\Scripts\python run.py --db inventory.sqlite --rate-limit 60

# 120 requetes/minute pour usage intensif
.\venv\Scripts\python run.py --db inventory.sqlite --rate-limit 120
```

**Comportement:**
- Limite par adresse IP
- Reponse 429 Too Many Requests si depassement
- Headers `X-RateLimit-*` inclus dans les reponses

### HTTPS (certificat SSL)

Pour un deploiement securise, deux options:

**Option 1: Certificat auto-signe (intranet)**

```bash
# Generer certificat (valide 1 an)
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes \
    -subj "/CN=inventory.local"

# Lancer avec SSL
cd server_display
.\venv\Scripts\uvicorn app.main:app \
    --host 0.0.0.0 --port 8000 \
    --ssl-keyfile key.pem --ssl-certfile cert.pem
```

**Option 2: Reverse proxy nginx (production)**

```nginx
server {
    listen 443 ssl;
    server_name inventory.example.com;

    ssl_certificate /etc/ssl/certs/inventory.crt;
    ssl_certificate_key /etc/ssl/private/inventory.key;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

**Option 3: Reverse proxy nginx sur un sous-chemin (deploiement actuel)**

En production, l'inventaire est servi derriere nginx sous le sous-chemin
`http://mn-panda/inventory/` (pas a la racine d'un domaine dedie). Config
correspondante dans `C:\nginx\conf\services.conf` :

```nginx
location /inventory/ {
    proxy_pass http://inventory/;   # upstream "inventory" = 127.0.0.1:8000
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

> **CONVENTION CRITIQUE — chemins relatifs obligatoires.**
> L'app etant servie sous `/inventory/` (et non `/`), **tous les chemins du
> front-end ET les URLs renvoyees par le backend doivent etre relatifs**
> (sans `/` initial), sinon le proxy sous-chemin casse.
> - HTML : `href="styles.css"`, `href="./"`, `src="app.js"`, `href="favicon.svg"`
> - JS  : `fetch("api/search")`, `fetch("data/...")`, `img.src = "pictograms/..."`
> - Backend : `sds_url = f"sds/{rel}"` dans `app/routes/search.py` (PAS `/sds/{rel}`)
>
> Un chemin absolu (`/styles.css`, `/api/...`) se resout contre la racine du
> proxy (`http://mn-panda/styles.css`) au lieu de `http://mn-panda/inventory/...`
> => CSS/JS/SDS en 404 et liens de nav renvoyant vers le dashboard. Les chemins
> relatifs marchent **a la fois** en acces direct (`:8000`) et derriere le proxy.

### Headers de securite

Les headers suivants sont automatiquement ajoutes a toutes les reponses:

| Header | Valeur | Protection |
|--------|--------|------------|
| `X-Content-Type-Options` | `nosniff` | MIME sniffing |
| `X-Frame-Options` | `DENY` | Clickjacking |
| `X-XSS-Protection` | `1; mode=block` | XSS reflecte |

### Variables d'environnement

Alternative aux arguments CLI:

```powershell
$env:EDIT_PASSWORD = "editeur456"
$env:READ_PASSWORD = "lecteur123"
$env:RATE_LIMIT = "60"

.\venv\Scripts\python run.py --db inventory.sqlite
```

### Arguments FastAPI (run.py)

| Argument | Default | Description |
|----------|---------|-------------|
| `--db` | inventory.sqlite | Inventory database path |
| `--host` | 0.0.0.0 | Server host |
| `--port` | 8000 | Server port |
| `--sds-root` | server_display/ | SDS files root directory |
| `--metrics-db` | server_display/metrics.sqlite | Metrics database path |
| `--edit-password` | (vide) | Mot de passe pour edition |
| `--read-password` | (vide) | Mot de passe pour lecture (vide = acces public) |
| `--rate-limit` | 60 | Limite requetes/minute par IP |
| `--backup-dir` | server_display/backups | Repertoire des backups |
| `--backup-keep` | 7 | Nombre de backups a conserver |
| `--workers` | 1 | Nombre de workers uvicorn |
| `--reload` | false | Auto-reload pour dev |

### Benchmark (single worker, WSL -> Windows)

| Endpoint | Requests | Concurrency | Throughput | P50 | P95 |
|----------|----------|-------------|------------|-----|-----|
| /api/search | 500 | 50 | 238 req/s | 1119ms | 2012ms |
| /api/columns | 500 | 50 | 560 req/s | 434ms | 807ms |
| /api/search | 1000 | 100 | 249 req/s | 2082ms | 3923ms |

*Note: Latence inclut le reseau WSL/Windows. 0 erreurs sur tous les tests.*

## Demarrage automatique au login Windows

Le serveur peut demarrer automatiquement a l'ouverture de session grace au Planificateur de taches Windows.

### Script de lancement

Fichier `server_display/start_server.bat` :

```bat
@echo off
cd /d D:\restart\00_Le_Code\04_picture_to_CAS\260304\server_display
.\venv\Scripts\python run.py --db inventory.sqlite --port 8000 --edit-password "test123"
```

### Enregistrer la tache planifiee (PowerShell admin)

```powershell
$action = New-ScheduledTaskAction -Execute "D:\restart\00_Le_Code\04_picture_to_CAS\260304\server_display\start_server.bat" -WorkingDirectory "D:\restart\00_Le_Code\04_picture_to_CAS\260304\server_display"
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "InventoryServer" -Action $action -Trigger $trigger -Settings $settings -Description "Chemical inventory FastAPI server"
```

### Gestion

```powershell
# Tester manuellement
Start-ScheduledTask -TaskName "InventoryServer"

# Voir la tache dans le GUI
taskschd.msc

# Supprimer la tache
Unregister-ScheduledTask -TaskName "InventoryServer" -Confirm:$false
```

**Comportement :** 3 tentatives de redemarrage automatique en cas de crash (intervalle 1 min).

## Monitoring avec Uptime Kuma

Dashboard de monitoring pour surveiller les serveurs Python (et autres).

- **Emplacement :** `D:\restart\uptime-kuma\`
- **Port :** 3001 (`http://localhost:3001`)
- **Version :** 2.2.1 (Node.js)

### Lancement manuel

```powershell
cd D:\restart\uptime-kuma
node server\server.js
```

### Demarrage automatique au login

Script `D:\restart\uptime-kuma\start_uptime_kuma.bat` + tache planifiee "UptimeKuma".

Enregistrement (PowerShell admin) :

```powershell
$action = New-ScheduledTaskAction -Execute "D:\restart\uptime-kuma\start_uptime_kuma.bat" -WorkingDirectory "D:\restart\uptime-kuma"
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "UptimeKuma" -Action $action -Trigger $trigger -Settings $settings -Description "Uptime Kuma monitoring dashboard"
```

### Configuration

1. Ouvrir `http://localhost:3001` (premier lancement = creation compte admin)
2. Ajouter un moniteur par serveur : Type HTTP, URL `http://localhost:8000`, Intervalle 60s

### Gestion

```powershell
# Tester manuellement
Start-ScheduledTask -TaskName "UptimeKuma"

# Supprimer
Unregister-ScheduledTask -TaskName "UptimeKuma" -Confirm:$false
```

## Legacy Server (server.py)

> ⚠️ **Deprecated.** `server.py` (ThreadingHTTPServer) est conserve comme
> reference/backup uniquement. La production utilise le serveur FastAPI
> (`run.py`, voir plus haut). N'utiliser que pour comparaison/depannage.

Arguments identiques au FastAPI server, utilise ThreadingHTTPServer.

```bash
cd server_display && python server.py --db inventory.sqlite
```

## Workflow SDS + Server

Quand `inventory.csv` est modifie manuellement, utiliser cette sequence:

```bash
# 1. Telecharger SDS manquantes
python find_sds-master/find_sds/find_sds.py --inventory server_display/inventory.csv --cas-column cas_numbers --download-path server_display/SDS --pool-size 5

# 2. Lier SDS au CSV
python server_display/scripts/link_sds_to_inventory.py --inventory server_display/inventory.csv --sds-dir server_display/SDS

# 3. Migrer vers SQLite
python server_display/scripts/migrate_csv_to_sqlite.py --csv server_display/inventory.csv --db server_display/inventory.sqlite

# 4. Auditer et valider SDS
cd opendata_to_db
python audit_sds.py --db ../server_display/inventory.sqlite --sds-dir ../server_display/SDS --apply

# 5. Enrichir H/P
python enrich_from_sds.py --db ../server_display/inventory.sqlite --sds-dir ../server_display/SDS
python enrich_inventory_pubchem.py --db ../server_display/inventory.sqlite

# 6. Lancer serveur (FastAPI)
cd ../server_display && venv/Scripts/python run.py --db inventory.sqlite
```

**IMPORTANT - Chemins de migration:**
```powershell
# Depuis server_display/ (recommande):
cd server_display
python scripts/migrate_csv_to_sqlite.py --csv inventory.csv --db inventory.sqlite

# Le CSV source est server_display/inventory.csv (~2400 lignes)
# NE PAS utiliser le inventory.csv a la racine du projet
```

**PowerShell launchers:**
```powershell
powershell -ExecutionPolicy Bypass -File .\run_server_display_with_sds.ps1
powershell -ExecutionPolicy Bypass -File .\start_server_display_0_0_0_0_8000.ps1
```

## Server Metrics

Le serveur collecte des metriques d'utilisation persistantes dans `server_display/metrics.sqlite`.

**Pages disponibles:**
- `/metrics.html` : Dashboard detaille des metriques
- `/icpe.html` : Recapitulatif ICPE + resume des metriques

**Metriques collectees:**
- Hits par endpoint (/, /api/search, /sds, /icpe.html, etc.)
- Termes de recherche (normalises, avec compteur)
- SDS consultes (par numero CAS)
- IPs uniques (par jour)

### API endpoints metriques

| Endpoint | Description |
|----------|-------------|
| `GET /api/metrics/summary` | Stats globales (totaux, IPs uniques) |
| `GET /api/metrics/daily?start=YYYY-MM-DD&end=YYYY-MM-DD` | Stats par jour |
| `GET /api/metrics/top-searches?limit=20` | Top recherches |
| `GET /api/metrics/top-sds?limit=20` | Top SDS consultes |

### Schema metrics.sqlite

```sql
metrics_daily (date, endpoint, count)        -- Hits par endpoint par jour
metrics_searches (date, query, count)        -- Recherches par jour
metrics_sds (date, cas_number, count)        -- SDS consultes par jour
metrics_ips (date, ip, first_seen)           -- IPs uniques par jour
```

## Inventory Editing

L'edition en ligne est **ouverte** : aucune authentification n'est requise (choix metier
intranet, voir « Decisions de deploiement »). Le serveur se lance sans `--edit-password`.

```powershell
# Depuis server_display/
.\venv\Scripts\python run.py --db inventory.sqlite
```

### Fonctionnalites

- Bouton "Modifier" sur chaque ligne (visible si edition activee)
- Bouton "+ Ajouter" pour creer une nouvelle entree
- Modal d'edition avec validation CAS
- Soft delete (entrees marquees, pas effacees)
- Gestion de version (conflits d'edition concurrente)
- Audit log de toutes les modifications

### Backup automatique

- Backup SQLite quotidien dans `server_display/backups/`
- Rotation automatique (7 derniers backups par defaut)

```powershell
.\venv\Scripts\python run.py --db inventory.sqlite --backup-dir backups --backup-keep 14
```

### API endpoints edition

| Endpoint | Methode | Description |
|----------|---------|-------------|
| `GET /api/edit-enabled` | GET | Renvoie toujours `true` (edition ouverte) |
| `GET /api/inventory/<rowid>` | GET | Recupere une ligne par rowid |
| `PUT /api/inventory/<rowid>` | PUT | Modifie une ligne (ouvert, rate-limite 10/min) |
| `POST /api/inventory` | POST | Cree une nouvelle ligne (ouvert, rate-limite 10/min) |
| `DELETE /api/inventory/<rowid>` | DELETE | Soft-delete une ligne (ouvert, rate-limite 10/min) |

### Acces

- Aucune authentification : les ecritures sont ouvertes sur l'intranet (choix metier).
- Garde-fous : rate-limit par IP, journal d'audit `edit_log`, soft-delete, backups quotidiens.

### Schema audit (table edit_log)

```sql
edit_log (
    id INTEGER PRIMARY KEY,
    inventory_rowid INTEGER,     -- Ligne modifiee
    action TEXT,                 -- 'create', 'update', 'delete'
    field_changes TEXT,          -- JSON des changements
    user_ip TEXT,                -- IP du client
    timestamp TEXT               -- Date/heure
)
```

## SDS Upload

Le serveur permet l'upload direct de fichiers SDS (PDF) via l'interface web.

### Fonctionnement

1. Cliquer sur le compteur "SDS X" (bleu) dans les statistiques
2. Selectionner un fichier PDF (max 100 MB)
3. Le serveur parse le PDF pour extraire automatiquement:
   - Le numero CAS (depuis Section 1 ou nom de fichier)
   - Le nom du produit (depuis Section 1)
4. Le fichier est sauvegarde dans `server_display/SDS/<CAS>-SDS.pdf`
5. Si un produit avec ce CAS existe, `sds_path` est mis a jour

### Identification du produit

- **Avec CAS:** Parsing du PDF (Section 1) ou nom de fichier (`<CAS>-SDS.pdf`)
  - Fichier sauvegarde sous `<CAS>-SDS.pdf`
  - Liaison par numero CAS dans l'inventaire
- **Sans CAS:** Utilise le nom du fichier comme identifiant
  - Fichier sauvegarde sous `<NomProduit>-SDS.pdf`
  - Liaison par nom de produit (recherche partielle, insensible a la casse)

### API endpoint

| Endpoint | Methode | Description |
|----------|---------|-------------|
| `POST /api/upload-sds` | POST | Upload SDS PDF avec parsing automatique |

### Reponse JSON

```json
{
  "success": true,
  "filename": "7732-18-5-SDS.pdf",
  "cas_number": "7732-18-5",
  "product_name": "Water",
  "size_mb": 1.5,
  "replaced_existing": false,
  "matched_products": [{"rowid": 42, "product_name": "Eau distillee", "cas_numbers": "7732-18-5"}],
  "products_linked": 1
}
```

**Dependance:** `pdfplumber` requis pour le parsing PDF (optionnel, fallback sur nom de fichier si absent)

## Export for LLM

Le bouton "Exporter" permet d'extraire les noms de produits pour les presenter a un LLM.

### Fonctionnement

1. Cliquer sur le bouton "Exporter" (bleu) dans la barre de controles
2. Un fichier `export_produits.txt` est telecharge
3. Format: un nom de produit par ligne (texte brut)
4. Si un filtre de recherche est actif, seuls les produits filtres sont exportes

### API endpoint

| Endpoint | Methode | Description |
|----------|---------|-------------|
| `GET /api/export?q=<filtre>&format=<txt\|json>` | GET | Exporte les noms de produits |

### Parametres

- `q` : filtre de recherche (optionnel, vide = tout l'inventaire)
- `format` : `txt` (defaut) ou `json`

### Format TXT (defaut)

```
Acide sulfurique 95%
Ethanol absolu
Sodium hydroxide pellets
```

### Format JSON

```json
{
  "products": [
    {
      "name": "Acide sulfurique 95%",
      "product_name": "Acide sulfurique 95%",
      "chemical_name": "Sulfuric acid",
      "cas": "7664-93-9",
      "iupac": "sulfuric acid"
    }
  ],
  "count": 1
}
```

## Import code_produit depuis Excel SMPC

```python
# Script one-shot pour ajouter la colonne code_produit depuis l'Excel SMPC
# Correspondance par numero CAS
import csv, openpyxl

# 1. Charger Excel (col C = Code Produit, col K = CAS)
wb = openpyxl.load_workbook("path/to/Inventaire_produits_chimique.xlsx", read_only=True)
cas_to_code = {str(row[10]).strip(): str(row[2]).strip()
               for row in wb.active.iter_rows(min_row=5, values_only=True)
               if row[2] and row[10]}

# 2. Mettre a jour inventory.csv
# ... ajouter colonne code_produit et mapper par CAS
```
