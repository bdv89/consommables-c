#!/usr/bin/env python3
"""Entry point for running the FastAPI server with uvicorn."""
from __future__ import annotations

import argparse
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Inventaire consommable bâtiment C (FastAPI + Uvicorn)")
    parser.add_argument("--db", default="consommable_batiment_c.sqlite", help="Inventory database path")
    parser.add_argument("--host", default="0.0.0.0", help="Server host")
    parser.add_argument("--port", type=int, default=8000, help="Server port")
    parser.add_argument(
        "--media-root",
        default=str(Path(__file__).parent),
        help="Media files root directory (serves photos/)",
    )
    parser.add_argument(
        "--metrics-db",
        default=str(Path(__file__).parent / "metrics.sqlite"),
        help="Metrics database path",
    )
    parser.add_argument(
        "--edit-password",
        default=os.environ.get("EDIT_PASSWORD", ""),
        help="Password for edit operations (empty = editing disabled)",
    )
    parser.add_argument(
        "--read-password",
        default=os.environ.get("READ_PASSWORD", ""),
        help="Password for read operations (empty = public access)",
    )
    parser.add_argument(
        "--rate-limit",
        type=int,
        default=int(os.environ.get("RATE_LIMIT", "60")),
        help="Rate limit: requests per minute per IP (default: 60)",
    )
    parser.add_argument(
        "--backup-dir",
        default=os.environ.get("BACKUP_DIR", str(Path(__file__).parent / "backups")),
        help="Directory for daily backups",
    )
    parser.add_argument(
        "--backup-keep",
        type=int,
        default=int(os.environ.get("BACKUP_KEEP", "7")),
        help="Number of daily backups to keep",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of uvicorn workers (default: 1, use >1 for production)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload for development",
    )
    args = parser.parse_args()

    # Validate database exists
    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")

    # Initialize settings
    from app.config import init_settings

    init_settings(
        db_path=db_path,
        host=args.host,
        port=args.port,
        media_root=Path(args.media_root),
        metrics_db=Path(args.metrics_db),
        edit_password=args.edit_password,
        read_password=args.read_password,
        rate_limit=args.rate_limit,
        backup_dir=Path(args.backup_dir),
        backup_keep=args.backup_keep,
    )

    # Run uvicorn
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        workers=args.workers if not args.reload else 1,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
