"""Inventory CRUD API endpoints."""
from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request

from app.dependencies import get_db, get_client_ip
from app.config import get_settings
from app.main import limiter
from app.utils import load_columns_async, ensure_daily_backup, next_code

router = APIRouter()


async def _log_edit(db, rowid: int, action: str, changes: dict, client_ip: str | None) -> None:
    """Log an edit action to the edit_log table."""
    await db.execute(
        "INSERT INTO edit_log (inventory_rowid, action, field_changes, user_ip) VALUES (?, ?, ?, ?)",
        (rowid, action, json.dumps(changes, ensure_ascii=False), client_ip),
    )


# FTS is kept in sync automatically by SQLite triggers (inventory_ai/au/ad) — see
# app.consommables_core.create_fts_triggers. No manual FTS maintenance here.


@router.get("/edit-enabled")
async def edit_enabled():
    """Editing is open (no password required); always enabled."""
    return {"enabled": True}


@router.get("/inventory/{rowid}")
async def get_inventory_row(
    rowid: int,
    db=Depends(get_db),
):
    """Get a single inventory row by rowid."""
    async with db.execute(
        "SELECT rowid, * FROM inventory WHERE rowid = ? AND deleted_at IS NULL",
        (rowid,),
    ) as cursor:
        row = await cursor.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Row not found")

    return {"row": dict(row)}


@router.post("/inventory")
@limiter.limit("10/minute")
async def create_inventory(
    request: Request,
    db=Depends(get_db),
    client_ip: str | None = Depends(get_client_ip),
):
    """Create a new inventory row (no auth; rate-limited)."""
    settings = get_settings()

    # Ensure daily backup
    ensure_daily_backup(settings.db_path, settings.backup_dir, settings.backup_keep)

    try:
        data = await request.json()
    except Exception:
        return {"success": False, "error": "Invalid JSON"}

    # `code` is the business key: auto-assign when absent, reject a duplicate otherwise.
    # The supplier `reference` is deliberately NOT constrained — the source data has both
    # duplicates (113-0720 on two glove sizes) and 12 articles with no reference at all.
    code = (data.get("code") or "").strip()
    async with db.execute("SELECT code FROM inventory") as cursor:
        existing = [row[0] for row in await cursor.fetchall()]

    if not code:
        code = next_code(existing)
    elif code in existing:
        return {"success": False, "error": f"Code déjà utilisé : {code}"}
    data["code"] = code

    columns = await load_columns_async(db)
    editable_cols = [c for c in columns if c not in ("version", "deleted_at")]
    insert_cols = [c for c in data.keys() if c in editable_cols]

    if not insert_cols:
        return {"success": False, "error": "No valid fields"}

    col_list = ", ".join(f'"{c}"' for c in insert_cols)
    placeholders = ", ".join(["?"] * len(insert_cols))
    values = [data.get(c, "") for c in insert_cols]

    cursor = await db.execute(
        f"INSERT INTO inventory ({col_list}, version) VALUES ({placeholders}, 1)",
        values,
    )
    new_rowid = cursor.lastrowid

    # Log the creation
    await _log_edit(db, new_rowid, "create", data, client_ip)

    # Opening movement, so SUM(delta) == quantite holds for this article from the start —
    # exactly like the import does. Without it the drift check would flag every new article.
    qte = int(data.get("quantite") or 0)
    if qte:
        await db.execute(
            "INSERT INTO stock_movement (article_code, delta, quantite_apres, motif, user_ip) "
            "VALUES (?, ?, ?, 'correction', ?)",
            (code, qte, qte, client_ip),
        )

    await db.commit()

    return {"success": True, "rowid": new_rowid, "code": code}


@router.put("/inventory/{rowid}")
@limiter.limit("10/minute")
async def update_inventory(
    rowid: int,
    request: Request,
    db=Depends(get_db),
    client_ip: str | None = Depends(get_client_ip),
):
    """Update an inventory row (no auth; rate-limited)."""
    settings = get_settings()

    # Ensure daily backup
    ensure_daily_backup(settings.db_path, settings.backup_dir, settings.backup_keep)

    try:
        data = await request.json()
    except Exception:
        return {"success": False, "error": "Invalid JSON"}

    expected_version = data.pop("_version", None)

    # Check row exists and version matches
    async with db.execute(
        "SELECT rowid, * FROM inventory WHERE rowid = ? AND deleted_at IS NULL",
        (rowid,),
    ) as cursor:
        current = await cursor.fetchone()

    if not current:
        return {"success": False, "error": "Row not found"}

    current_dict = dict(current)
    current_version = current_dict.get("version") or 1

    if expected_version is not None and current_version != expected_version:
        return {
            "success": False,
            "error": "Version conflict - row was modified",
            "current_version": current_version,
        }

    # Filter to valid editable columns.
    # `quantite` is excluded on purpose: every stock change must go through
    # POST /api/movements so the journal and the quantity can never disagree. A silent
    # UPDATE here would be the one way to make SUM(delta) != quantite.
    columns = await load_columns_async(db)
    editable_cols = [
        c for c in columns if c not in ("version", "deleted_at", "quantite")
    ]
    update_cols = [c for c in data.keys() if c in editable_cols]

    if not update_cols:
        return {"success": False, "error": "No valid fields to update"}

    # Build changes dict (old -> new)
    changes = {}
    for col in update_cols:
        old_val = current_dict.get(col)
        new_val = data[col]
        if old_val != new_val:
            changes[col] = [old_val, new_val]

    if not changes:
        return {"success": True, "message": "No changes", "rowid": rowid}

    # Build UPDATE statement
    set_clause = ", ".join(f'"{c}" = ?' for c in changes.keys())
    values = [data[c] for c in changes.keys()]
    values.append(rowid)

    await db.execute(
        f"UPDATE inventory SET {set_clause}, version = version + 1 WHERE rowid = ?",
        values,
    )

    # Log the update
    await _log_edit(db, rowid, "update", changes, client_ip)

    await db.commit()

    return {
        "success": True,
        "rowid": rowid,
        "updated_fields": list(changes.keys()),
        "new_version": current_version + 1,
    }


@router.delete("/inventory/{rowid}")
@limiter.limit("10/minute")
async def delete_inventory(
    rowid: int,
    request: Request,
    db=Depends(get_db),
    client_ip: str | None = Depends(get_client_ip),
):
    """Soft delete an inventory row (no auth; rate-limited)."""
    settings = get_settings()

    # Ensure daily backup
    ensure_daily_backup(settings.db_path, settings.backup_dir, settings.backup_keep)

    # Check row exists
    async with db.execute(
        "SELECT rowid FROM inventory WHERE rowid = ? AND deleted_at IS NULL",
        (rowid,),
    ) as cursor:
        current = await cursor.fetchone()

    if not current:
        return {"success": False, "error": "Row not found"}

    # Soft delete
    now = datetime.now().isoformat()
    await db.execute(
        "UPDATE inventory SET deleted_at = ? WHERE rowid = ?",
        (now, rowid),
    )

    # Log the deletion
    await _log_edit(db, rowid, "delete", {"deleted_at": now}, client_ip)

    # FTS stays in sync via the AFTER UPDATE trigger; search filters deleted_at IS NULL.
    await db.commit()

    return {"success": True, "rowid": rowid}
