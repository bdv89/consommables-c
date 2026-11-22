"""Utility functions for the FastAPI app."""
from __future__ import annotations

import re
import shutil
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite


def safe_path(base: Path, rel: str) -> Path:
    """Validate and resolve a relative path within a base directory.

    Raises ValueError if the path would escape the base directory.
    """
    rel = rel.lstrip("/\\")
    candidate = (base / rel).resolve()
    if not str(candidate).startswith(str(base.resolve())):
        raise ValueError("Unsafe path")
    return candidate


def _strip_accents(s: str) -> str:
    """Lowercase and drop accents for vocabulary matching (é -> e)."""
    return "".join(
        c for c in unicodedata.normalize("NFD", s.lower())
        if unicodedata.category(c) != "Mn"
    )


async def load_search_vocab(db: "aiosqlite.Connection") -> tuple[dict, dict]:
    """Load the controlled vocabularies (categories, locations) from the DB.

    Returns (categories, emplacements): dicts mapping accent-stripped-lowercase key ->
    canonical value. Cheap (two SELECT DISTINCT on a small table); loaded per request so
    a newly created category is immediately routable.
    """
    def _vocab(rows) -> dict[str, str]:
        out: dict[str, str] = {}
        for row in rows:
            v = (row[0] or "").strip()
            if v:
                out[_strip_accents(v)] = v
        return out

    async with db.execute(
        "SELECT DISTINCT categorie FROM inventory WHERE categorie IS NOT NULL AND categorie != ''"
    ) as cursor:
        categories = _vocab(await cursor.fetchall())

    async with db.execute(
        "SELECT DISTINCT emplacement FROM inventory WHERE emplacement IS NOT NULL AND emplacement != ''"
    ) as cursor:
        emplacements = _vocab(await cursor.fetchall())

    return categories, emplacements


def build_match_query(
    q: str, categories: dict | None = None, emplacements: dict | None = None
) -> str:
    """Build an FTS5 MATCH query from a search string.

    Smart routing: a token matching a known category is scoped to `categorie:` and a known
    location to `emplacement:` (both exact, no prefix), so "EPI Mezzanine" becomes
    `categorie:"EPI" AND emplacement:"Mezzanine"` instead of a loose prefix bleeding across
    every column. Unknown tokens keep the multi-column prefix behavior (`"token"*`).

    Categories take precedence if a token were ever in both vocabularies.
    """
    tokens = [t for t in re.split(r"\s+", q.strip()) if t]
    if not tokens:
        return ""

    categories = categories or {}
    emplacements = emplacements or {}

    parts = []
    for t in tokens:
        key = _strip_accents(t)
        if key in categories:
            canon = categories[key].replace('"', '""')
            parts.append(f'categorie:"{canon}"')
        elif key in emplacements:
            canon = emplacements[key].replace('"', '""')
            parts.append(f'emplacement:"{canon}"')
        else:
            safe = t.replace('"', '""')
            parts.append(f'"{safe}"*')
    return " AND ".join(parts)


def escape_like(value: str) -> str:
    """Neutralise LIKE wildcards so a typed '%' stays a literal percent sign.

    Used with `ESCAPE '\\'`: without it, a lone '%' typed in the template picker matches
    every article and a '_' matches any single character.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# Fields worth suggesting from existing values (small controlled vocabularies).
SUGGEST_FIELDS = ["categorie", "emplacement", "unite", "fournisseur"]


async def load_field_values(db: "aiosqlite.Connection", fields: list[str]) -> dict:
    """Return distinct non-empty values per field, ordered by frequency (desc).

    Used to populate <datalist> autocompletion in the add/edit form. Field names come
    from a fixed allow-list (SUGGEST_FIELDS), never from user input.
    """
    result: dict[str, list[str]] = {}
    for f in fields:
        async with db.execute(
            f'SELECT "{f}" FROM inventory '
            f'WHERE "{f}" IS NOT NULL AND "{f}" != \'\' AND deleted_at IS NULL '
            f'GROUP BY "{f}" ORDER BY COUNT(*) DESC'
        ) as cursor:
            rows = await cursor.fetchall()
        result[f] = [row[0] for row in rows]
    return result


# Internal article code: a stable business key, independent of the supplier reference
# (which has duplicates and gaps in the source data).
CODE_PREFIX = "CONS-"
_CODE_RE = re.compile(r"^" + re.escape(CODE_PREFIX) + r"(\d+)$")


def next_code(codes) -> str:
    """Suggest the next article code: prefix + (max existing number + 1), 3-digit padded.

    Only codes matching exactly `CONS-<digits>` count; anything else is ignored. Empty
    inventory starts at CONS-001. max+1 (never count+1) so a deleted article never causes
    a code to be reused.
    """
    nums = []
    for c in codes:
        if not c:
            continue
        m = _CODE_RE.match(str(c).strip())
        if m:
            nums.append(int(m.group(1)))
    n = (max(nums) + 1) if nums else 1
    return f"{CODE_PREFIX}{n:03d}"


def load_columns(conn) -> list[str]:
    """Load column names from inventory table."""
    if hasattr(conn, 'execute'):
        # Sync sqlite3 connection
        return [row[1] for row in conn.execute("PRAGMA table_info(inventory)")]
    raise TypeError("Expected sqlite3 connection")


async def load_columns_async(db: "aiosqlite.Connection") -> list[str]:
    """Load column names from inventory table (async)."""
    async with db.execute("PRAGMA table_info(inventory)") as cursor:
        rows = await cursor.fetchall()
    return [row[1] for row in rows]


async def has_fts(db: "aiosqlite.Connection") -> bool:
    """Check if FTS5 index exists."""
    async with db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='inventory_fts'"
    ) as cursor:
        row = await cursor.fetchone()
    return row is not None


# Backup tracking
_backup_done_today: set[str] = set()


def ensure_daily_backup(db_path: Path, backup_dir: Path | None, backup_keep: int = 7) -> None:
    """Create a daily backup if not already done today."""
    if not backup_dir:
        return

    today = datetime.now().strftime("%Y%m%d")
    if today in _backup_done_today:
        return

    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"inventory_{today}.sqlite"

    if not backup_path.exists():
        shutil.copy2(db_path, backup_path)
        print(f"Backup created: {backup_path}")

        # Cleanup old backups
        backups = sorted(backup_dir.glob("inventory_*.sqlite"), reverse=True)
        for old_backup in backups[backup_keep:]:
            old_backup.unlink()
            print(f"Deleted old backup: {old_backup}")

    _backup_done_today.add(today)
