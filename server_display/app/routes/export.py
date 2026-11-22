"""Export API endpoint."""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse, JSONResponse

from app.dependencies import get_db, get_client_ip, check_read_auth
from app.main import limiter
from app.utils import build_match_query, load_columns_async, has_fts, load_search_vocab

router = APIRouter()

EXPORT_FIELDS = ["code", "designation", "reference", "categorie", "emplacement",
                 "quantite", "unite", "contenu_par_unite", "seuil_min", "fournisseur"]


@router.get("/export")
@limiter.limit("10/minute")
async def export_articles(
    request: Request,
    q: str = Query(""),
    format: str = Query("csv"),
    low: int = Query(0),
    db=Depends(get_db),
    client_ip: str | None = Depends(get_client_ip),
    _auth: bool = Depends(check_read_auth),
):
    """Export the current selection as CSV (Excel-friendly, `;`) or JSON.

    Honors the same search string as the table, so "export what I'm looking at" works —
    which is what a restocking list actually is.
    """
    from app.routes.metrics import log_request_async

    await log_request_async("/api/export", client_ip)

    columns = await load_columns_async(db)
    use_fts = await has_fts(db)
    # Same filter as /api/search, so "Exporter" always yields what the table shows —
    # in particular the restocking list behind the "sous seuil" toggle.
    deleted_filter = "deleted_at IS NULL"
    if low:
        deleted_filter += " AND seuil_min > 0 AND quantite < seuil_min"
    select_cols = ", ".join(f'"{c}"' for c in EXPORT_FIELDS)
    order = " ORDER BY code COLLATE NOCASE"

    if q.strip():
        categories, emplacements = await load_search_vocab(db)
        match = build_match_query(q.strip(), categories, emplacements)
        if use_fts and match:
            sql = (
                f"SELECT {', '.join('inventory.' + c for c in EXPORT_FIELDS)} "
                "FROM inventory_fts "
                "JOIN inventory ON inventory_fts.rowid = inventory.rowid "
                f"WHERE inventory_fts MATCH ? AND inventory.{deleted_filter}"
                " ORDER BY inventory.code COLLATE NOCASE"
            )
            async with db.execute(sql, (match,)) as cursor:
                rows = await cursor.fetchall()
        else:
            like = f"%{q.strip()}%"
            where = " OR ".join([f'"{c}" LIKE ?' for c in columns])
            async with db.execute(
                f"SELECT {select_cols} FROM inventory WHERE ({where}) AND {deleted_filter}{order}",
                [like] * len(columns),
            ) as cursor:
                rows = await cursor.fetchall()
    else:
        async with db.execute(
            f"SELECT {select_cols} FROM inventory WHERE {deleted_filter}{order}"
        ) as cursor:
            rows = await cursor.fetchall()

    records = [{c: row[c] for c in EXPORT_FIELDS} for row in rows]

    if format.lower() == "json":
        return JSONResponse(
            content={"articles": records, "count": len(records)},
            headers={"Content-Disposition": 'attachment; filename="consommables.json"'},
        )

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=EXPORT_FIELDS, delimiter=";",
                            lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    for r in records:
        writer.writerow({k: ("" if v is None else v) for k, v in r.items()})

    # BOM so Excel opens the accented designations as UTF-8 instead of mojibake.
    return PlainTextResponse(
        content="﻿" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="consommables.csv"'},
    )
