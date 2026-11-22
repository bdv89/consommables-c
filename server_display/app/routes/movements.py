"""Stock movement API: the only write path for `inventory.quantite`.

Quantity and journal are updated in a single transaction, so the two can never disagree.
The journal is keyed on the article `code` (a business key), not on rowid, so the history
survives a re-import of the xlsx.

There is no authentication anywhere in this app (LAN, open editing) — a movement records
the client IP and the timestamp, not a person.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from app.config import get_settings
from app.consommables_core import MOTIFS
from app.dependencies import get_db, get_client_ip, check_read_auth
from app.main import limiter
from app.utils import ensure_daily_backup

router = APIRouter()


@router.post("/movements")
@limiter.limit("30/minute")
async def create_movement(
    request: Request,
    db=Depends(get_db),
    client_ip: str | None = Depends(get_client_ip),
):
    """Apply a stock movement: {code, delta, motif}. Returns the resulting quantity.

    Async twin of `consommables_core.apply_movement` (which the scripts and tests use on a
    plain sqlite3 connection). Both enforce the same two rules: unknown article and
    negative resulting stock are refused, and nothing is written when they are.
    """
    settings = get_settings()
    ensure_daily_backup(settings.db_path, settings.backup_dir, settings.backup_keep)

    try:
        data = await request.json()
    except Exception:
        return {"success": False, "error": "Invalid JSON"}

    code = (data.get("code") or "").strip()
    try:
        delta = int(data.get("delta"))
    except (TypeError, ValueError):
        return {"success": False, "error": "delta doit être un entier"}

    if not delta:
        return {"success": False, "error": "delta nul : rien à enregistrer"}

    motif = (data.get("motif") or "").strip() or ("reappro" if delta > 0 else "sortie")
    if motif not in MOTIFS:
        return {"success": False, "error": f"Motif inconnu : {motif}"}

    async with db.execute(
        "SELECT quantite FROM inventory WHERE code = ? AND deleted_at IS NULL", (code,)
    ) as cursor:
        row = await cursor.fetchone()

    if row is None:
        return {"success": False, "error": f"Article inconnu : {code}"}

    current = row[0] or 0
    after = current + delta
    if after < 0:
        return {
            "success": False,
            "error": f"Stock insuffisant : {current} en stock, mouvement {delta:+d}",
            "quantite": current,
        }

    await db.execute(
        "UPDATE inventory SET quantite = ?, version = version + 1 WHERE code = ?",
        (after, code),
    )
    await db.execute(
        "INSERT INTO stock_movement (article_code, delta, quantite_apres, motif, user_ip) "
        "VALUES (?, ?, ?, ?, ?)",
        (code, delta, after, motif, client_ip),
    )
    await db.commit()

    return {"success": True, "code": code, "delta": delta, "quantite": after, "motif": motif}


@router.post("/movements/set")
@limiter.limit("30/minute")
async def set_quantity(
    request: Request,
    db=Depends(get_db),
    client_ip: str | None = Depends(get_client_ip),
):
    """Set an absolute quantity: {code, quantite}. Journalled as a `correction` delta.

    Typing a recount straight into the table is the natural gesture, but storing it as an
    absolute value would break the journal. So the delta is computed here and the movement
    goes down the same path as any other.
    """
    try:
        data = await request.json()
    except Exception:
        return {"success": False, "error": "Invalid JSON"}

    code = (data.get("code") or "").strip()
    try:
        target = int(data.get("quantite"))
    except (TypeError, ValueError):
        return {"success": False, "error": "quantite doit être un entier"}

    if target < 0:
        return {"success": False, "error": "La quantité ne peut pas être négative"}

    async with db.execute(
        "SELECT quantite FROM inventory WHERE code = ? AND deleted_at IS NULL", (code,)
    ) as cursor:
        row = await cursor.fetchone()

    if row is None:
        return {"success": False, "error": f"Article inconnu : {code}"}

    current = row[0] or 0
    delta = target - current
    if not delta:
        return {"success": True, "code": code, "quantite": current, "delta": 0}

    settings = get_settings()
    ensure_daily_backup(settings.db_path, settings.backup_dir, settings.backup_keep)

    await db.execute(
        "UPDATE inventory SET quantite = ?, version = version + 1 WHERE code = ?",
        (target, code),
    )
    await db.execute(
        "INSERT INTO stock_movement (article_code, delta, quantite_apres, motif, user_ip) "
        "VALUES (?, ?, ?, 'correction', ?)",
        (code, delta, target, client_ip),
    )
    await db.commit()

    return {"success": True, "code": code, "quantite": target, "delta": delta,
            "motif": "correction"}


@router.get("/movements")
@limiter.limit("60/minute")
async def list_movements(
    request: Request,
    code: str = Query(""),
    motif: str = Query(""),
    limit: int = Query(200),
    offset: int = Query(0),
    db=Depends(get_db),
    _auth: bool = Depends(check_read_auth),
):
    """Paginated journal, newest first, joined with the article designation."""
    where, params = ["1=1"], []
    if code.strip():
        where.append("m.article_code = ?")
        params.append(code.strip())
    if motif.strip():
        where.append("m.motif = ?")
        params.append(motif.strip())
    clause = " AND ".join(where)

    async with db.execute(
        f"SELECT COUNT(1) FROM stock_movement m WHERE {clause}", params
    ) as cursor:
        total = (await cursor.fetchone())[0]

    # LEFT JOIN: a movement whose article was deleted still belongs in the journal.
    async with db.execute(
        f"SELECT m.id, m.article_code, m.delta, m.quantite_apres, m.motif, m.user_ip, "
        f"       m.timestamp, i.designation "
        f"FROM stock_movement m LEFT JOIN inventory i ON i.code = m.article_code "
        f"WHERE {clause} ORDER BY m.id DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ) as cursor:
        rows = [dict(r) for r in await cursor.fetchall()]

    return {"total": total, "returned": len(rows), "motifs": list(MOTIFS), "rows": rows}
