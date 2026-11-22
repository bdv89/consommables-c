"""Search and columns API endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from app.dependencies import get_db, get_client_ip, check_read_auth
from app.main import limiter
from app.utils import (
    build_match_query,
    escape_like,
    load_columns_async,
    has_fts,
    load_search_vocab,
    load_field_values,
    next_code,
    SUGGEST_FIELDS,
)

router = APIRouter()


@router.get("/search")
@limiter.limit("60/minute")
async def search(
    request: Request,
    q: str = Query(""),
    sort: str = Query(""),
    dir: str = Query("asc"),
    low: int = Query(0),
    limit: int = Query(200),
    offset: int = Query(0),
    db=Depends(get_db),
    client_ip: str | None = Depends(get_client_ip),
    _auth: bool = Depends(check_read_auth),
):
    """Search inventory with FTS5 or LIKE fallback."""
    from app.routes.metrics import log_request_async, log_search_async

    await log_request_async("/api/search", client_ip)

    columns = await load_columns_async(db)
    use_fts = await has_fts(db)

    order_dir = "DESC" if dir.lower() == "desc" else "ASC"
    order_sql = ""
    if sort and sort in columns:
        order_sql = f' ORDER BY "{sort}" COLLATE NOCASE {order_dir}'

    # The "below threshold" toggle is a server-side filter on purpose: filtering the
    # current page client-side would silently hide the low-stock articles sitting on the
    # other pages — the exact ones the toggle exists to surface.
    deleted_filter = "deleted_at IS NULL"
    if low:
        deleted_filter += " AND seuil_min > 0 AND quantite < seuil_min"

    if q.strip():
        # Log search query
        await log_search_async(q.strip())

        categories, emplacements = await load_search_vocab(db)
        match = build_match_query(q.strip(), categories, emplacements)
        if use_fts and match:
            base_sql = (
                " FROM inventory_fts "
                " JOIN inventory ON inventory_fts.rowid = inventory.rowid "
                f" WHERE inventory_fts MATCH ? AND inventory.{deleted_filter}"
            )
            async with db.execute(f"SELECT COUNT(1){base_sql}", (match,)) as cursor:
                total = (await cursor.fetchone())[0]

            sql = "SELECT inventory.rowid, inventory.*" + base_sql
            if order_sql:
                sql += order_sql
            else:
                # Weight matches: what the thing IS outranks where it comes from.
                # Column order: code, designation, reference, categorie, emplacement,
                # fournisseur, remarque (cf. consommables_core.FTS_COLUMNS).
                sql += " ORDER BY bm25(inventory_fts, 8.0, 10.0, 3.0, 5.0, 2.0, 1.0, 1.0)"
            sql += " LIMIT ? OFFSET ?"

            async with db.execute(sql, (match, limit, offset)) as cursor:
                rows = await cursor.fetchall()
        else:
            # Fallback: LIKE on all text columns
            like = f"%{q.strip()}%"
            where = " OR ".join([f'"{c}" LIKE ?' for c in columns])

            async with db.execute(
                f"SELECT COUNT(1) FROM inventory WHERE ({where}) AND {deleted_filter}",
                [like] * len(columns),
            ) as cursor:
                total = (await cursor.fetchone())[0]

            sql = f"SELECT rowid, * FROM inventory WHERE ({where}) AND {deleted_filter}"
            sql += order_sql if order_sql else ""
            sql += " LIMIT ? OFFSET ?"

            async with db.execute(sql, [like] * len(columns) + [limit, offset]) as cursor:
                rows = await cursor.fetchall()
    else:
        async with db.execute(f"SELECT COUNT(1) FROM inventory WHERE {deleted_filter}") as cursor:
            total = (await cursor.fetchone())[0]

        sql = f"SELECT rowid, * FROM inventory WHERE {deleted_filter}"
        if order_sql:
            sql += order_sql
        sql += " LIMIT ? OFFSET ?"

        async with db.execute(sql, (limit, offset)) as cursor:
            rows = await cursor.fetchall()

    data = [dict(row) for row in rows]

    # Global counts: deliberately NOT `deleted_filter`, which may carry the low-stock
    # filter — a "12 sous seuil" badge that only counts the filtered slice would read 12
    # of 12 and stop meaning anything. Only soft-deletion is excluded here.
    async with db.execute("SELECT COUNT(1) FROM inventory WHERE deleted_at IS NULL") as cursor:
        total_all = (await cursor.fetchone())[0]

    # seuil_min > 0 is what makes a threshold real; 0 means alerting is off for that article.
    async with db.execute(
        "SELECT COUNT(1) FROM inventory WHERE deleted_at IS NULL "
        "AND seuil_min > 0 AND quantite < seuil_min"
    ) as cursor:
        count_sous_seuil = (await cursor.fetchone())[0]

    async with db.execute(
        "SELECT COUNT(1) FROM inventory WHERE deleted_at IS NULL AND quantite <= 0"
    ) as cursor:
        count_rupture = (await cursor.fetchone())[0]

    return {
        "total": total,
        "returned": len(data),
        "total_all": total_all,
        "count_sous_seuil": count_sous_seuil,
        "count_rupture": count_rupture,
        "rows": data,
    }


@router.get("/columns")
@limiter.limit("60/minute")
async def get_columns(
    request: Request,
    db=Depends(get_db),
    client_ip: str | None = Depends(get_client_ip),
    _auth: bool = Depends(check_read_auth),
):
    """Get list of inventory columns."""
    from app.routes.metrics import log_request_async

    await log_request_async("/api/columns", client_ip)
    columns = await load_columns_async(db)
    return {"columns": columns}


@router.get("/suggestions")
@limiter.limit("60/minute")
async def get_suggestions(
    request: Request,
    db=Depends(get_db),
    _auth: bool = Depends(check_read_auth),
):
    """Distinct existing values for small-vocab fields, for <datalist> autocompletion."""
    return await load_field_values(db, SUGGEST_FIELDS)


# The three ways an article gets identified at the bench. Closed list: column names are
# never built from user input.
SUGGEST_SEARCH_COLUMNS = ("designation", "reference", "code")
SUGGEST_MAX_LIMIT = 50


@router.get("/suggest")
@limiter.limit("60/minute")
async def suggest(
    request: Request,
    q: str = Query(""),
    limit: int = Query(20),
    db=Depends(get_db),
    _auth: bool = Depends(check_read_auth),
):
    """Existing articles offered as templates when creating a new one.

    A flat list, not the grouped one of the chemical inventory this is forked from: there
    1 fiche covered N bottles and a representative row had to be elected, here 1 row = 1
    article and `code` is UNIQUE.

    Beyond saving typing, this is the duplicate guard: two rows for the same article would
    split its stock across both and break its threshold, so the caller sees what already
    exists before creating anything.
    """
    limit = max(1, min(limit, SUGGEST_MAX_LIMIT))
    term = q.strip()

    # A designation *starting* with the typed text outranks one merely containing it —
    # applied on top of whichever engine ran the search.
    prefix_rank = "CASE WHEN inventory.designation LIKE ? ESCAPE '\\' THEN 0 ELSE 1 END"
    prefix_param = f"{escape_like(term)}%"

    if not term:
        # Without a query, the articles created last are the ones most likely to be
        # declined into a variant (another glove size, another syringe gauge).
        sql = "SELECT rowid, * FROM inventory WHERE deleted_at IS NULL ORDER BY rowid DESC LIMIT ?"
        params: list = [limit]
    else:
        # Same engine as /api/search, on purpose: the picker must not grow a second search
        # semantics. "gants XL" has to find "boite gants en latex taille XL", which a
        # single `%gants XL%` never does — FTS matches each word separately, and folds
        # accents along the way ("mecanique" finds "mécanique").
        categories, emplacements = await load_search_vocab(db)
        match = build_match_query(term, categories, emplacements)

        if await has_fts(db) and match:
            sql = (
                "SELECT inventory.rowid, inventory.* FROM inventory_fts "
                "JOIN inventory ON inventory_fts.rowid = inventory.rowid "
                "WHERE inventory_fts MATCH ? AND inventory.deleted_at IS NULL "
                f"ORDER BY {prefix_rank}, "
                # Same weights as /api/search: what the thing IS outranks where it's from.
                "bm25(inventory_fts, 8.0, 10.0, 3.0, 5.0, 2.0, 1.0, 1.0) LIMIT ?"
            )
            params = [match, prefix_param, limit]
        else:
            # Fallback without FTS. Tokenised the same way: every word must appear
            # somewhere, though not necessarily in the same column.
            words = term.split()
            per_word = " AND ".join(
                "(" + " OR ".join(f'"{c}" LIKE ? ESCAPE \'\\\'' for c in SUGGEST_SEARCH_COLUMNS) + ")"
                for _ in words
            )
            params = []
            for word in words:
                params.extend([f"%{escape_like(word)}%"] * len(SUGGEST_SEARCH_COLUMNS))
            sql = (
                f"SELECT rowid, * FROM inventory WHERE deleted_at IS NULL AND ({per_word}) "
                f"ORDER BY {prefix_rank.replace('inventory.', '')}, "
                '"designation" COLLATE NOCASE LIMIT ?'
            )
            params.extend([prefix_param, limit])

    async with db.execute(sql, params) as cursor:
        rows = [dict(row) for row in await cursor.fetchall()]

    return {"q": term, "returned": len(rows), "rows": rows}


@router.get("/next-code")
@limiter.limit("60/minute")
async def get_next_code(
    request: Request,
    db=Depends(get_db),
    _auth: bool = Depends(check_read_auth),
):
    """Suggest the next article code (max existing number + 1, 3-digit padded).

    Reads soft-deleted rows too: a deleted article must not free its code for reuse, or
    the stock_movement journal — keyed on that code — would gain a second, unrelated history.
    """
    async with db.execute("SELECT code FROM inventory") as cursor:
        codes = [row[0] for row in await cursor.fetchall()]
    return {"next": next_code(codes)}
