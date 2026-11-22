# Troubleshooting

> ⚠️ **Document hérité de l'outil chimie d'origine.** Ce dépôt est le fork
> **inventaire de cibles** : les fonctions chimie (CAS, PubChem, phrases H/P, ICPE) en ont été
> retirées, et le serveur tourne sur le port 8001. Vérifier chaque affirmation avant de
> l'appliquer ici. Référence à jour : [inventaire-cibles.md](inventaire-cibles.md).


Guide de resolution des problemes courants.

## Erreurs CAS courantes

### Types d'erreurs

| Type d'erreur | Exemple | Cause |
|---------------|---------|-------|
| **Erreur OCR** | `13478-19-9` au lieu de `13478-10-9` | Confusion 0/9, 1/7 |
| **Checksum invalide** | `77050-84-9` | CAS inexistant |
| **Confusion hydrate** | `7791-18-6` (MgCl2) vs `13478-10-9` (FeCl2) | Meme structure cristalline |
| **Forme anhydre/hydratee** | `7758-94-3` (FeCl2) vs `13478-10-9` (FeCl2-4H2O) | Composes differents |

### Diagnostic

```bash
# Lister les CAS invalides (checksum echoue)
python validate_cas.py --db server_display/inventory.sqlite --check

# Rapport complet (JSON)
python validate_cas.py --db server_display/inventory.sqlite --report > cas_report.json
```

### Correction

```bash
# Mode interactif: proposer corrections
python validate_cas.py --db server_display/inventory.sqlite --interactive

# Corriger un CAS specifique
python validate_cas.py --db server_display/inventory.sqlite --fix 2227 --new-cas 13478-10-9
```

### Lecons apprises

1. **Toujours verifier le contenu SDS** - Le nom du fichier peut correspondre a un autre compose
2. **PubChem GHS souvent absent pour hydrates** - Utiliser la SDS comme source primaire
3. **Sources SDS automatisees limitees** - Recherche web manuelle parfois necessaire

## Sources SDS alternatives

Quand les scripts automatises echouent, essayer manuellement:

| Fournisseur | URL | Notes |
|-------------|-----|-------|
| **Carl Roth** | carlroth.com | Recherche par CAS, PDF direct |
| **Sigma-Aldrich** | sigmaaldrich.com | Large catalogue |
| **Fisher Scientific** | fishersci.com | SDS section dediee |
| **TCI Chemicals** | tcichemicals.com | Produits japonais |
| **Alfa Aesar** | alfa.com | Metaux et inorganiques |

## Limitations des sources automatisees

| Source | Status | Notes |
|--------|--------|-------|
| **Fisher Scientific** | OK (Playwright) | Recommande, SDS 2025 |
| ChemBlink | OK | SDS datees (2015-2018) |
| Carl Roth | Limite | 18:00-04:00 UTC uniquement |
| Sigma-Aldrich | Bloque | Anti-bot agressif |
| Merck Millipore | Bloque | HTTP 403 |
| ECHA | Bloque | HTTP 403 |

## Problemes serveur

### Port deja utilise

```bash
# Linux/WSL
lsof -i :8000
kill <PID>

# Windows PowerShell
netstat -ano | findstr :8000
taskkill /PID <PID> /F
```

### Base de donnees verrouillee

```bash
# Verifier les connexions actives
sqlite3 server_display/inventory.sqlite ".tables"

# Si bloque, arreter tous les processus Python
pkill -f "python.*server"
```

### Metriques ne s'enregistrent pas

Verifier que le fichier `metrics.sqlite` est accessible en ecriture:

```bash
ls -la server_display/metrics.sqlite
# Si inexistant, le serveur le creera au demarrage
```

## Problemes d'enrichissement

### PubChem rate limit

Si vous voyez des erreurs 429 (Too Many Requests):

```bash
# Reduire la vitesse en ajoutant des delais
# ou traiter par lots plus petits
python enrich_inventory_pubchem.py --db server_display/inventory.sqlite --limit 50
```

### SDS non parsees

Verifier que pdfplumber est installe dans le venv:

```bash
venv/bin/pip list | grep pdfplumber

# Installer si absent
venv/bin/pip install pdfplumber
```

### Dates SDS non extraites

Certains PDFs ont des formats de date non standard. Verifier manuellement:

```bash
# Extraire le texte des premieres pages
venv/bin/python -c "
import pdfplumber
with pdfplumber.open('server_display/SDS/64-17-5-SDS.pdf') as pdf:
    for i, page in enumerate(pdf.pages[:3]):
        print(f'--- Page {i+1} ---')
        print(page.extract_text()[:1000])
"
```

## Logs et debug

### Activer le mode debug SDS

```bash
python find_sds-master/find_sds/find_sds.py --debug --inventory inventory.csv --cas-column cas_numbers --download-path SDS
```

### Mode debug Playwright (navigateur visible)

```bash
venv/bin/python download_sds_browser.py --cas 64-17-5 --no-headless --sds-dir SDS
```

### Verifier les conflits d'enrichissement

```bash
cat opendata_to_db/enrichment_conflicts.json | python -m json.tool
```
