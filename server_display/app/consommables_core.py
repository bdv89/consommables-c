"""Schema and pure domain rules for the consumables inventory.

Single source of truth for: the DB schema, the deduction rules applied at import, the
stock-movement transaction, and the low-stock predicate. Every consumer imports from
here — the import script, the FastAPI routes, the alert script and the tests — so a rule
can never drift between the write paths (the cibles fork had `emplacement_from_reference`
in three copies and paid for it).

Deliberately framework-free: no FastAPI, no aiosqlite. `create_schema` and
`apply_movement` take a plain `sqlite3.Connection`, so the standalone scripts reuse the
exact same logic without pulling the web stack.
"""
from __future__ import annotations

import re
import sqlite3
import unicodedata
from typing import Mapping, Sequence

# --------------------------------------------------------------------------- schema

INVENTORY_COLUMNS = [
    "code",               # CONS-001… stable business key (see app.utils.next_code)
    "designation",        # what the thing is (ex-DESCRIPTION column)
    "reference",          # supplier reference — free text, duplicates and blanks tolerated
    "categorie",
    "emplacement",
    "quantite",           # authoritative stock level
    "unite",              # pièce, paire, boîte, sachet, paquet, rouleau, carton
    "contenu_par_unite",  # "100" for a "sachet de 100"; "" when not a pack
    "seuil_min",          # 0 = alerting disabled for this article
    "fournisseur",
    "photo",              # relative path under media_root, served by GET /media/{path}
    "remarque",
]

# Integer columns; everything else is TEXT.
NUMERIC_COLUMNS = {"quantite", "seuil_min"}

FTS_COLUMNS = ["code", "designation", "reference", "categorie", "emplacement",
               "fournisseur", "remarque"]

MOTIFS = ("import", "sortie", "reappro", "correction")


def create_fts_triggers(conn: sqlite3.Connection) -> None:
    """Triggers that keep the external-content FTS index in sync on every write.

    Canonical FTS5 external-content pattern (the special 'delete' command with OLD.*
    values), which — unlike a plain DELETE by rowid — never corrupts the index.
    Column names in FTS_COLUMNS are plain identifiers, so no quoting needed.
    """
    cols = ", ".join(FTS_COLUMNS)
    newv = ", ".join(f"new.{c}" for c in FTS_COLUMNS)
    oldv = ", ".join(f"old.{c}" for c in FTS_COLUMNS)
    for name in ("inventory_ai", "inventory_au", "inventory_ad"):
        conn.execute(f"DROP TRIGGER IF EXISTS {name}")
    conn.execute(
        f"CREATE TRIGGER inventory_ai AFTER INSERT ON inventory BEGIN "
        f"INSERT INTO inventory_fts(rowid, {cols}) VALUES (new.rowid, {newv}); END"
    )
    conn.execute(
        f"CREATE TRIGGER inventory_ad AFTER DELETE ON inventory BEGIN "
        f"INSERT INTO inventory_fts(inventory_fts, rowid, {cols}) "
        f"VALUES ('delete', old.rowid, {oldv}); END"
    )
    conn.execute(
        f"CREATE TRIGGER inventory_au AFTER UPDATE ON inventory BEGIN "
        f"INSERT INTO inventory_fts(inventory_fts, rowid, {cols}) "
        f"VALUES ('delete', old.rowid, {oldv}); "
        f"INSERT INTO inventory_fts(rowid, {cols}) VALUES (new.rowid, {newv}); END"
    )


def create_schema(conn: sqlite3.Connection, drop_movements: bool = False) -> None:
    """(Re)create the inventory schema.

    Drops `inventory` / `inventory_fts` / `edit_log`. `stock_movement` is preserved by
    default: it is keyed on the BUSINESS code, not on rowid, so the history survives a
    re-import of the xlsx untouched — same rule that kept the cibles document links alive.
    """
    col_defs = ", ".join(
        f'"{c}" {"INTEGER DEFAULT 0" if c in NUMERIC_COLUMNS else "TEXT"}'
        for c in INVENTORY_COLUMNS
    )
    conn.execute("DROP TABLE IF EXISTS inventory")
    conn.execute(
        f"CREATE TABLE inventory ({col_defs}, version INTEGER DEFAULT 1, deleted_at TEXT)"
    )
    conn.execute("CREATE UNIQUE INDEX idx_code ON inventory(code)")
    conn.execute("CREATE INDEX idx_categorie ON inventory(categorie)")

    conn.execute("DROP TABLE IF EXISTS inventory_fts")
    fts_cols = ", ".join(f'"{c}"' for c in FTS_COLUMNS)
    conn.execute(
        "CREATE VIRTUAL TABLE inventory_fts USING fts5("
        f"{fts_cols}, content='inventory', content_rowid='rowid')"
    )
    create_fts_triggers(conn)

    conn.execute("DROP TABLE IF EXISTS edit_log")
    conn.execute(
        """
        CREATE TABLE edit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inventory_rowid INTEGER NOT NULL,
            action TEXT NOT NULL,
            field_changes TEXT,
            user_ip TEXT,
            timestamp TEXT DEFAULT (datetime('now'))
        )
        """
    )

    if drop_movements:
        conn.execute("DROP TABLE IF EXISTS stock_movement")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS stock_movement (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_code   TEXT NOT NULL,
            delta          INTEGER NOT NULL,
            quantite_apres INTEGER NOT NULL,
            motif          TEXT,
            user_ip        TEXT,
            timestamp      TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mvt_article ON stock_movement(article_code, id DESC)"
    )


# ------------------------------------------------------------------------ movements


def apply_movement(
    conn: sqlite3.Connection,
    code: str,
    delta: int,
    motif: str,
    user_ip: str | None,
) -> int:
    """Apply a stock movement and journal it atomically. Returns the resulting quantity.

    Raises ValueError if the article is unknown or the movement would drive the stock
    negative — in both cases nothing is written, so the journal never records a movement
    that did not happen.
    """
    row = conn.execute(
        "SELECT quantite FROM inventory WHERE code = ? AND deleted_at IS NULL", (code,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Article inconnu : {code}")

    current = row[0] or 0
    after = current + delta
    if after < 0:
        raise ValueError(f"Stock insuffisant : {current} en stock, mouvement {delta:+d}")

    conn.execute(
        "UPDATE inventory SET quantite = ?, version = version + 1 WHERE code = ?",
        (after, code),
    )
    conn.execute(
        "INSERT INTO stock_movement (article_code, delta, quantite_apres, motif, user_ip) "
        "VALUES (?, ?, ?, ?, ?)",
        (code, delta, after, motif, user_ip),
    )
    conn.commit()
    return after


# ------------------------------------------------------------------------ low stock


def low_stock_rows(rows: Sequence[Mapping]) -> list[dict]:
    """Articles below their threshold, worst shortfall first.

    Strict predicate `quantite < seuil_min`, and `seuil_min <= 0` means "alerting
    disabled" — so an article at 0 with no threshold stays silent. Same semantics the
    cibles thresholds used; the surprise is worth stating rather than discovering.
    """
    low = []
    for row in rows:
        seuil = int(row["seuil_min"] or 0)
        if seuil <= 0:
            continue
        qte = int(row["quantite"] or 0)
        if qte < seuil:
            low.append({
                "code": row["code"],
                "designation": row["designation"],
                "quantite": qte,
                "seuil_min": seuil,
                "manque": seuil - qte,
            })
    return sorted(low, key=lambda r: (-r["manque"], r["code"]))


# ------------------------------------------------------------------ deduction rules


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s.lower())
        if unicodedata.category(c) != "Mn"
    )


# A container word counts as packaging ONLY when followed by an explicit count.
# Without that guard, "boite pétri", "boite à pizza" and "sachet papier" — where the
# container word belongs to the PRODUCT NAME — would be read as packaging, turning
# 1000 Petri dishes into 1000 boxes.
_PACK_RE = re.compile(
    r"\b(sachet|boite|paquet|carton|rouleau)s?\s+(?:de|d')\s*(\d+)", re.IGNORECASE
)
_CANON_UNIT = {
    "sachet": "sachet", "boite": "boîte", "paquet": "paquet",
    "carton": "carton", "rouleau": "rouleau",
}


def deduce_unite(designation: str) -> tuple[str, str]:
    """Return (unite, contenu_par_unite) deduced from the designation.

    Order matters: an explicit "<container> de <N>" wins, then pairs, then rolls and
    cartons as units in their own right, else "pièce".
    """
    low = _strip_accents(designation or "")

    m = _PACK_RE.search(low)
    if m:
        return _CANON_UNIT[m.group(1).lower()], m.group(2)
    if re.search(r"\bpaires?\b", low):
        return "paire", ""
    if re.search(r"\brouleau\b|\bfilm plastique\b", low):
        return "rouleau", ""
    if re.search(r"\bcarton de\b", low):
        return "carton", ""
    return "pièce", ""


# Ordered: the first matching rule wins, so the specific precedes the generic.
# "boite pétri" is labware, and must be tested before the "boite/sac" packaging rule.
_CATEGORIE_RULES: list[tuple[str, str]] = [
    (r"petri", "Consommables labo"),
    (r"gant|masque|lunette|combinaison|casquette|charlotte|bouchons d'oreilles|surlunette",
     "EPI"),
    (r"bouteille|fiole|pissette|entonnoir|becher|eprouvette|lamelle", "Verrerie"),
    (r"seringue|aiguille|sterican|neolus|fine-ject|henke-ject", "Seringues"),
    (r"kapton|scotch|adhesif|papier collant|ruban", "Adhésifs"),
    (r"eponge|nettoyant|nettoyage|buvard|tampon|villeda|papier bleu", "Nettoyage"),
    (r"stylo|diamant", "Outillage"),
    (r"filtre|papiers? filtre|papier ph|ecouvillon|coton tige|pipette|pince|pots? pour pesee",
     "Consommables labo"),
    (r"\bsacs?\b|\bboite\b|carton|gobelet|pizza|bac a bec|sachet papier|aluminium",
     "Emballage"),
]


def deduce_categorie(designation: str) -> str:
    """Deduce a category from the designation, or "" when no rule matches.

    An empty category is honest — it shows up in the UI as something to fill in, which
    beats a wrong bucket that nobody ever revisits.
    """
    low = _strip_accents(designation or "")
    for pattern, categorie in _CATEGORIE_RULES:
        if re.search(pattern, low):
            return categorie
    return ""


_VWR_REF = re.compile(r"^\d{3}-\d{4}$")


def deduce_fournisseur(reference: str) -> str:
    """VWR catalogue references have a fixed NNN-NNNN shape; anything else is unknown."""
    return "VWR" if _VWR_REF.match((reference or "").strip()) else ""


# Two references crammed into one cell: "112-5101 ---> MAPA34710091", "825-14AM / 817-0055".
# The slash MUST be surrounded by whitespace: "SINGNY100/7" is one reference whose /7 is a
# glove size, and splitting it collapsed the four sizes onto one duplicated reference.
# "8833 - 111-0451" stays whole for the same reason (a lone dash is not a separator).
_REF_SEPARATOR = re.compile(r"\s*--+>\s*|\s+/\s+")


def split_references(raw: str) -> tuple[str, str]:
    """Return (primary_reference, other_references) — the rest goes to `remarque`."""
    s = (raw or "").strip()
    if not s:
        return "", ""
    parts = [p.strip() for p in _REF_SEPARATOR.split(s) if p.strip()]
    if len(parts) <= 1:
        return s, ""
    return parts[0], " / ".join(parts[1:])
