"""Metrics API endpoints and logging functions."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Query

from app.config import get_settings

router = APIRouter()

# Module-level connection for metrics logging
_metrics_db: Optional[aiosqlite.Connection] = None


async def init_metrics():
    """Initialize metrics database and create tables if needed."""
    global _metrics_db
    settings = get_settings()

    _metrics_db = await aiosqlite.connect(settings.metrics_db)
    _metrics_db.row_factory = aiosqlite.Row

    # Create tables
    await _metrics_db.execute("""
        CREATE TABLE IF NOT EXISTS metrics_daily (
            date TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            count INTEGER DEFAULT 0,
            PRIMARY KEY (date, endpoint)
        )
    """)
    await _metrics_db.execute("""
        CREATE TABLE IF NOT EXISTS metrics_searches (
            date TEXT NOT NULL,
            query TEXT NOT NULL,
            count INTEGER DEFAULT 0,
            PRIMARY KEY (date, query)
        )
    """)
    await _metrics_db.execute("""
        CREATE TABLE IF NOT EXISTS metrics_ips (
            date TEXT NOT NULL,
            ip TEXT NOT NULL,
            first_seen TEXT,
            PRIMARY KEY (date, ip)
        )
    """)
    await _metrics_db.commit()

    print(f"Metrics DB: {settings.metrics_db}")


async def close_metrics():
    """Close metrics database connection."""
    global _metrics_db
    if _metrics_db:
        await _metrics_db.close()
        _metrics_db = None


async def log_request_async(endpoint: str, client_ip: str | None = None):
    """Log a request to the metrics database."""
    if not _metrics_db:
        return

    today = datetime.now().strftime("%Y-%m-%d")

    # Update endpoint count
    await _metrics_db.execute("""
        INSERT INTO metrics_daily (date, endpoint, count) VALUES (?, ?, 1)
        ON CONFLICT(date, endpoint) DO UPDATE SET count = count + 1
    """, (today, endpoint))

    # Log unique IP
    if client_ip:
        now = datetime.now().isoformat()
        await _metrics_db.execute("""
            INSERT OR IGNORE INTO metrics_ips (date, ip, first_seen) VALUES (?, ?, ?)
        """, (today, client_ip, now))

    await _metrics_db.commit()


async def log_search_async(query: str):
    """Log a search query to the metrics database."""
    if not _metrics_db:
        return

    today = datetime.now().strftime("%Y-%m-%d")

    # Normalize query
    normalized = re.sub(r"\s+", " ", query.strip().lower())
    if not normalized:
        return

    await _metrics_db.execute("""
        INSERT INTO metrics_searches (date, query, count) VALUES (?, ?, 1)
        ON CONFLICT(date, query) DO UPDATE SET count = count + 1
    """, (today, normalized))

    await _metrics_db.commit()


@router.get("/summary")
async def metrics_summary():
    """Return metrics summary."""
    if not _metrics_db:
        return {"error": "Metrics not initialized"}

    # Total hits by endpoint with first/last seen
    async with _metrics_db.execute(
        """SELECT endpoint, SUM(count) as total, MIN(date) as first_seen, MAX(date) as last_seen
           FROM metrics_daily GROUP BY endpoint ORDER BY total DESC"""
    ) as cursor:
        endpoints = [
            {
                "endpoint": r["endpoint"],
                "total": r["total"],
                "first_seen": r["first_seen"],
                "last_seen": r["last_seen"],
            }
            for r in await cursor.fetchall()
        ]

    # Unique IPs today
    today = datetime.now().strftime("%Y-%m-%d")
    async with _metrics_db.execute(
        "SELECT COUNT(DISTINCT ip) as count FROM metrics_ips WHERE date = ?", (today,)
    ) as cursor:
        row = await cursor.fetchone()
        unique_ips_today = row["count"] if row else 0

    # Total unique IPs
    async with _metrics_db.execute("SELECT COUNT(DISTINCT ip) as count FROM metrics_ips") as cursor:
        row = await cursor.fetchone()
        unique_ips_total = row["count"] if row else 0

    # Total searches
    async with _metrics_db.execute("SELECT SUM(count) as total FROM metrics_searches") as cursor:
        row = await cursor.fetchone()
        total_searches = row["total"] if row and row["total"] else 0

    # Date range
    async with _metrics_db.execute(
        "SELECT MIN(date) as start_date, MAX(date) as end_date FROM metrics_daily"
    ) as cursor:
        row = await cursor.fetchone()
        date_range = {
            "start": row["start_date"] if row else None,
            "end": row["end_date"] if row else None,
        }

    return {
        "endpoints": endpoints,
        "unique_ips_today": unique_ips_today,
        "unique_ips_total": unique_ips_total,
        "total_searches": total_searches,
        "date_range": date_range,
        "generated_at": datetime.now().isoformat(),
    }


@router.get("/daily")
async def metrics_daily(
    start: str = Query(None),
    end: str = Query(None),
):
    """Return daily metrics."""
    if not _metrics_db:
        return {"error": "Metrics not initialized"}

    # Build date filter
    params = []
    where = ""
    if start:
        where += " AND date >= ?"
        params.append(start)
    if end:
        where += " AND date <= ?"
        params.append(end)

    # Daily totals with unique IPs
    async with _metrics_db.execute(
        f"""SELECT d.date, SUM(d.count) as total,
                   (SELECT COUNT(DISTINCT ip) FROM metrics_ips i WHERE i.date = d.date) as unique_ips
            FROM metrics_daily d WHERE 1=1{where}
            GROUP BY d.date ORDER BY d.date DESC""",
        params,
    ) as cursor:
        totals = [
            {"date": r["date"], "total": r["total"], "unique_ips": r["unique_ips"] or 0}
            for r in await cursor.fetchall()
        ]

    # Daily details by endpoint
    async with _metrics_db.execute(
        f"SELECT date, endpoint, count FROM metrics_daily WHERE 1=1{where} ORDER BY date DESC, count DESC",
        params,
    ) as cursor:
        details = [dict(r) for r in await cursor.fetchall()]

    return {"totals": totals, "details": details}


@router.get("/top-searches")
async def top_searches(
    limit: int = Query(20),
    start: str = Query(None),
):
    """Return top search queries."""
    if not _metrics_db:
        return {"error": "Metrics not initialized"}

    params = [limit]
    where = ""
    if start:
        where = " WHERE date >= ?"
        params.insert(0, start)

    async with _metrics_db.execute(
        f"SELECT query, SUM(count) as total FROM metrics_searches{where} GROUP BY query ORDER BY total DESC LIMIT ?",
        params,
    ) as cursor:
        searches = [{"query": r["query"], "count": r["total"]} for r in await cursor.fetchall()]

    return {"searches": searches}
