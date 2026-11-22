# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Inventaire consommable bâtiment C** — inventory web app for lab **consumables** (PPE, glassware,
syringes, packaging, adhesives…). Fork of the *cibles* (sputtering targets) inventory, which
was itself a fork of a chemical-label OCR tool. The generic server stack (FastAPI + SQLite +
FTS5 + web UI) is reused; the per-physical-item model is replaced by **quantity management**.

- **1 row = 1 article carrying a quantity** (87 items), loaded from
  `Inventaire consomato.xlsx` (sheet `inventaire Mezzanine`).
- **Source of truth = the web app** after the initial import (one-shot/destructive).
- No auth on writes (rate-limited only); optional read password. Fine on LAN — revisit if exposed.
- **Live since 2026-08-10** at <http://mn-panda/consommable_batiment_c/> — NSSM service
  `InventoryBatimentC` on port 8003.

> 📄 **Primary detailed doc: [docs/inventaire-consommable-batiment-c.md](docs/inventaire-consommable-batiment-c.md)** —
> schema, endpoints, deduction rules, movement journal, UI, commands. Read it first.

> ⏳ **Pending: the folder is still named `consommables_chips`.** Everything else already
> carries the `consommable_batiment_c` name (service, URL, tile, task, DB, code, doc); the
> service and `send_alert.bat` hold an explicit fallback on the old path meanwhile. A folder
> cannot be renamed while a process has it as its working directory — and a Claude Code
> session pins its shells there. **Close the session**, then from an elevated PowerShell
> located elsewhere run
> `serveur_automate/deploy/Rename-And-Deploy-BatimentC.ps1`, and drop both fallbacks
> afterwards.

**Naming convention.** The app's *identity* is `consommable_batiment_c` (folder, service,
URL, tile, DB, task). The *domain* is still "consommables", which is why module names keep
it: `app/consommables_core.py`, `static/consommables.css`,
`scripts/import_consommables_xlsx.py`. They describe what the code handles, not which
instance runs it. Do not rename them.

### The model shift that drives everything

|  | Cibles (the fork's origin) | Consommables (here) |
|---|---|---|
| 1 row = | 1 physical object | 1 **article** carrying a quantity |
| Stock = | `COUNT(*)` of non-`Finie` rows | the `quantite` column |
| Threshold = | per category `materiau\|forme\|dimension` | per **article** (`seuil_min`) |
| State | Neuve/Utilisée/Finie | ✗ not applicable |
| Traceability | `edit_log` | `edit_log` + **`stock_movement` journal** |

**Core scripts:**

| Script | Description |
|--------|-------------|
| `server_display/scripts/import_consommables_xlsx.py` | Import xlsx → SQLite (position mapping, unit/category/supplier deduction, `CONS-NNN` codes, FTS + triggers, one opening movement per article). One-shot destructive; prints an **import report** of what the rules could not decide. |
| `server_display/scripts/send_low_stock_email.py` | Daily low-stock digest: e-mails the articles where `quantite < seuil_min` (reuses `app.consommables_core.low_stock_rows`). Read-only; SMTP via env vars. **Live since 2026-08-10**: scheduled task `BatimentCLowStockAlert` (SYSTEM, daily 08:00), M365 direct send, no credentials. ⚠️ Silent until at least one article has `seuil_min >= 1`. |
| `server_display/run.py` | FastAPI server entry point (uvicorn). |

**Directories:**

| Directory | Purpose |
|-----------|---------|
| `server_display/` | FastAPI app, static UI, SQLite DB, venv |
| `server_display/photos/` | Article photos (served via `GET /media/{path}`) |
| `server_display/backups/` | Daily DB backups |
| `standards/` | Methodology prisms referenced by `CLAUDE.local.md` |

**Deployment scripts** live outside this repo, in
`06_unified_work/serveur_automate/deploy/`:

| Script | Purpose |
|--------|---------|
| `Add-InventoryBatimentC-Service.ps1` | Create/start the NSSM service, reload nginx, restart Dashboard, register the alert task. Idempotent. |
| `Remove-InventoryBatimentC-Service.ps1` | Full rollback (service, task, nginx block, tile). Leaves data untouched. |
| `Rename-And-Deploy-BatimentC.ps1` | Folder rename + deploy — run with the Claude Code session **closed**. |

## Quick Start

```bash
# venv (already created): server_display/venv
cd server_display && venv/Scripts/python.exe -m pip install -r requirements-server.txt pytest httpx

# --- Run the import FROM THE REPO ROOT: its default paths are relative to it. ---
python server_display/scripts/import_consommables_xlsx.py \
    --xlsx "Inventaire consomato.xlsx" --sheet "inventaire Mezzanine" \
    --db server_display/consommable_batiment_c.sqlite

# Start server for local dev (pick a free port — 8000/8001/8002 are the other inventories,
# and 8003 is now this app's own production service `InventoryBatimentC`)
cd server_display && venv/Scripts/python.exe run.py --db consommable_batiment_c.sqlite --port 8042
# Production: NSSM service `InventoryBatimentC` on port 8003, served at
# http://mn-panda/consommable_batiment_c/ (see docs/inventaire-consommable-batiment-c.md "Déploiement")

# Read-protected (writes stay open):
cd server_display && venv/Scripts/python.exe run.py --db consommable_batiment_c.sqlite --read-password "lecteur"

# Tests
cd server_display && venv/Scripts/python.exe -m pytest tests/
```

## Server CLI (run.py)

| Argument | Default | Description |
|----------|---------|-------------|
| `--db` | consommable_batiment_c.sqlite | Inventory database path |
| `--host` | 0.0.0.0 | Server host |
| `--port` | 8000 | Server port |
| `--media-root` | server_display/ | Media files root (serves `photos/`) |
| `--metrics-db` | server_display/metrics.sqlite | Metrics database |
| `--read-password` | (empty) | Password for reading (empty = public). Writing is always open. |
| `--rate-limit` | 60 | Requests/minute per IP |
| `--backup-dir` | server_display/backups | Backup directory |
| `--backup-keep` | 7 | Backups to retain |

## Database Schema (server_display/consommable_batiment_c.sqlite)

| Table | Purpose |
|-------|---------|
| `inventory` | Main data, 1 row/article (+ `version`, `deleted_at`) |
| `inventory_fts` | FTS5 external-content index (triggers keep it in sync) |
| `stock_movement` | Append-only stock journal, keyed on `article_code` |
| `edit_log` | Audit log (rowid, action, field_changes JSON, user_ip, timestamp) |

**`inventory` columns:** `code, designation, reference, categorie, emplacement, quantite,
unite, contenu_par_unite, seuil_min, fournisseur, photo, remarque`.

- `code` (`CONS-001`…) is the **business key** (UNIQUE index). `reference` is deliberately
  **unconstrained** free text: the source has a real duplicate (`113-0720` on two glove
  sizes) and 12 articles with no reference at all.
- `quantite` / `seuil_min` are INTEGER. **`seuil_min = 0` means "alerting disabled"**.
- FTS-indexed: `code, designation, reference, categorie, emplacement, fournisseur, remarque`.

## Key Technical Details

- **One source of truth**: the DDL *and* every domain rule live in
  `app/consommables_core.py`, imported by the import script, the routes, the alert script
  **and the test fixture**. The cibles fork kept `emplacement_from_reference` in three
  copies and documented the pain; do not reintroduce that pattern.
- **The stock invariant**: `quantite` is authoritative, and every change writes the journal
  line *and* the quantity in one transaction. `PUT /api/inventory/{rowid}` **excludes
  `quantite`** on purpose — a direct UPDATE is the only way to make `SUM(delta)` diverge
  from `quantite`. Drift check (must return 0 rows):
  ```sql
  SELECT code FROM inventory i WHERE quantite !=
    (SELECT COALESCE(SUM(delta),0) FROM stock_movement WHERE article_code = i.code);
  ```
- **`stock_movement.article_code`, never `rowid`**: rowids are reassigned on every import,
  business keys survive. Same rule that kept the cibles document links alive.
- **Unit deduction needs an explicit count** (`<container> de <N>`). A naive `\bboite\b`
  rule classifies "boite **pétri**" (1000 units!), "boite **à pizza**" and "sachet
  **papier**" as packaging, where the container word is part of the *product name*.
  Test: `tests/test_consommables.py::test_deduce_unite_container_word_in_product_name_stays_piece`.
- **Reference splitting**: `/` separates two references **only when surrounded by spaces**.
  `SINGNY100/7` is one reference whose `/7` is a glove size; splitting it fabricated a
  duplicate across the four sizes.
- **Smart search** (`app/utils.py::build_match_query` + `load_search_vocab`): known category
  tokens → `categorie:"X"`, known locations → `emplacement:"Y"` (exact), unknown →
  multi-column prefix; bm25 weights `designation`×10, `code`×8, `categorie`×5.
- **Global counters stay global**: `count_sous_seuil` / `count_rupture` ignore the `low`
  filter. A "12 below threshold" badge counting only the filtered slice would read 12 of 12.
- **UI**: quantity cell *is* the control (`[−] 33 [+]`); stock state shown as a **word**, not
  colour alone (WCAG 1.4.1); "Sous seuil" filters **server-side** (client-side filtering
  would hide the low articles on other pages); all columns toggleable
  (`localStorage` key `columnVisibilityConsommables`); dark mode; `/` focuses search.
- **Creation goes through a template picker** (`GET /api/suggest` + step 1 of the edit modal,
  ported from the chemical inventory). "Reprendre cette fiche" clones everything **except
  `code` and `quantite`** — the code keys the movement journal and the quantity belongs to it.
  Its real job is the duplicate guard: two rows for one article split its stock and break its
  threshold, hence the "Ouvrir cette fiche" button next to it.
  **The picker has no search of its own** — it reuses `build_match_query` + FTS like
  `/api/search`. A whole-phrase `LIKE '%gants XL%'` found nothing, since no designation
  carries those two words side by side ("boite gants en latex taille XL"). Keep the no-FTS
  fallback tokenised, or the bug comes back through it.
- **Layout = 5 bands, one responsibility each** (`body.app-screen`, index page only —
  `styles.css` is shared with `movements.html`/`metrics.html`, which keep normal scrolling):
  ① site header ② `.controls` act ③ `.columns-bar` configure ④ `.table-wrap` data + sort
  ⑤ `.statusbar` locate + paginate.
- **Four CSS traps inherited from the cibles fork** — all still apply, documented in
  [docs/inventaire-consommable-batiment-c.md](docs/inventaire-consommable-batiment-c.md) § Interface:
  1. `position: sticky` on `th` **never engages** without a bounded-height `overflow` ancestor.
  2. **Never put a `transition` on a property fed by a theme variable** — it freezes on the
     previous theme after a toggle.
  3. `table-layout: fixed` + per-column widths decouple table width from data quality.
  4. A flex item with `margin: 0 auto` **does not stretch** — it needs `width: 100%`.
- **Asset cache-busting**: `styles.css?v=38`, `consommables.css?v=2`, `theme.js?v=4`,
  `app.js?v=2`, `edit.js?v=2`, `movements-app.js?v=1`, `favicon.svg?v=1`.
  **Bump the `?v=` whenever you edit one**, or browsers keep serving the cached copy and the
  fix looks like it did nothing. `styles.css`/`theme.js` are loaded by all three pages —
  bump all three. No service restart needed (FastAPI re-reads `static/` per request).

## Development Context & Philosophy

### Default Approach
- Evolutionary POC: get end-to-end working ASAP
- Simple modules and standalone scripts
- Deterministic and traceable implementations
- Reasonable defaults; minimal configuration
- Clear logging over sophisticated error handling

### Solution Hierarchy
1. Remove complexity or unnecessary step
2. Repurpose an existing tool in the stack
3. Minimally modify existing code
4. Add new code (last resort)

### Forbidden Practices
- No premature abstractions or micro-services
- No heavy configuration for stable parameters
- No exotic error handling for rare edge cases
- No premature optimization before clear profiling

## Documentation

| Document | Content |
|----------|---------|
| [docs/inventaire-consommable-batiment-c.md](docs/inventaire-consommable-batiment-c.md) | **Primary** — schema, API, deduction rules, movement journal, UI, commands |
| [docs/server-operation.md](docs/server-operation.md) | Server setup, scheduled task, backups (generic parts still apply) |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Common errors and solutions |
| [docs/inventaire-cibles.md](docs/inventaire-cibles.md) | Lineage — the fork this one comes from (the live app lives in `../inventaire_cibles/`) |
