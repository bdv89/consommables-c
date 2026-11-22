#!/usr/bin/env python3
"""Daily low-stock digest: e-mail the articles whose stock is below their threshold.

Reads the inventory from SQLite, reuses the exact predicate the app uses
(app.consommables_core.low_stock_rows), and — if any article is below its threshold —
sends a single recap e-mail via smtplib (stdlib, no extra dependency).

⚠️ `seuil_min = 0` means "alerting disabled": with no threshold set, no mail will ever go
out. Use --always to tell "chain broken" apart from "nothing to report".

Read-only on the database. Meant to be run once a day by the Windows Task Scheduler.

SMTP is configured through environment variables (secrets never live in the code):
    SMTP_HOST       required to actually send (e.g. company relay, or smtp.gmail.com)
    SMTP_PORT       default 587
    SMTP_USER       optional (login user); if empty, no authentication is attempted
    SMTP_PASSWORD   optional (login password) — never logged
    SMTP_STARTTLS   "true"/"false", default "true"
    ALERT_TO        recipient(s), comma-separated, required (no default — set per deployment)
    ALERT_FROM      sender, default = SMTP_USER or ALERT_TO

Usage:
    python scripts/send_low_stock_email.py --db consommable_batiment_c.sqlite
    python scripts/send_low_stock_email.py --dry-run     # print the mail, don't send
    python scripts/send_low_stock_email.py --always      # send even when nothing is low
"""
from __future__ import annotations

import argparse
import os
import smtplib
import sqlite3
import sys
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path

# Make `app` importable when run as a standalone script from server_display/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.consommables_core import low_stock_rows  # noqa: E402


# --- Data access -----------------------------------------------------------

def load_below_articles(conn: sqlite3.Connection) -> list[dict]:
    """Return the articles currently below their threshold (empty list if none)."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT code, designation, quantite, seuil_min, unite, emplacement "
        "FROM inventory WHERE deleted_at IS NULL"
    ).fetchall()
    return low_stock_rows(rows)


# --- E-mail building -------------------------------------------------------

def build_email_body(below: list[dict]) -> str:
    """Plain-text digest body listing every below-threshold article."""
    if not below:
        return "Aucun article sous le seuil. Tout est OK.\n"
    lines = [
        f"{len(below)} article(s) sous le seuil de stock :",
        "",
    ]
    for art in below:
        rupture = " — RUPTURE" if art["quantite"] == 0 else ""
        lines.append(
            f"  - {art['designation']} ({art['code']}) : "
            f"{art['quantite']} en stock / seuil {art['seuil_min']}"
            f" — manque {art['manque']}{rupture}"
        )
    lines += ["", "— Inventaire consommable bâtiment C (alerte automatique quotidienne)"]
    return "\n".join(lines) + "\n"


def build_message(below: list[dict], sender: str, recipients: list[str]) -> EmailMessage:
    msg = EmailMessage()
    subject = (
        f"[Consommables Bât. C] {len(below)} article(s) sous le seuil"
        if below else "[Consommables Bât. C] Stock OK"
    )
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.set_content(build_email_body(below))
    return msg


# --- SMTP ------------------------------------------------------------------

@dataclass
class SmtpConfig:
    host: str
    port: int = 587
    user: str = ""
    password: str = ""
    starttls: bool = True
    sender: str = ""
    recipients: tuple[str, ...] = ()


def smtp_config_from_env() -> SmtpConfig:
    recipients = tuple(
        addr.strip()
        for addr in os.environ.get("ALERT_TO", "").split(",")
        if addr.strip()
    )
    user = os.environ.get("SMTP_USER", "")
    return SmtpConfig(
        host=os.environ.get("SMTP_HOST", ""),
        port=int(os.environ.get("SMTP_PORT", "587")),
        user=user,
        password=os.environ.get("SMTP_PASSWORD", ""),
        starttls=os.environ.get("SMTP_STARTTLS", "true").strip().lower() != "false",
        sender=os.environ.get("ALERT_FROM", "") or user or (recipients[0] if recipients else ""),
        recipients=recipients,
    )


def send_email(msg: EmailMessage, cfg: SmtpConfig) -> None:
    """Send the message via SMTP. STARTTLS and login only when configured."""
    with smtplib.SMTP(cfg.host, cfg.port, timeout=30) as smtp:
        if cfg.starttls:
            smtp.starttls()
        if cfg.user:
            smtp.login(cfg.user, cfg.password)
        smtp.send_message(msg)


# --- Entry point -----------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="E-mail the low-stock articles digest")
    parser.add_argument("--db", default="consommable_batiment_c.sqlite", help="Inventory SQLite path")
    parser.add_argument("--always", action="store_true",
                        help="Send a mail even when nothing is below threshold")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the e-mail instead of sending it")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    try:
        below = load_below_articles(conn)
    finally:
        conn.close()

    print(f"{len(below)} article(s) sous le seuil.")

    if not below and not args.always:
        print("RAS — aucun mail envoyé.")
        return 0

    cfg = smtp_config_from_env()
    msg = build_message(below, cfg.sender, list(cfg.recipients))

    if args.dry_run:
        print("--- DRY RUN — mail non envoyé ---")
        print(f"To: {msg['To']}")
        print(f"Subject: {msg['Subject']}")
        print(msg.get_content())
        return 0

    if not cfg.host:
        print("ERREUR : SMTP_HOST non défini — impossible d'envoyer. "
              "Utilise --dry-run pour tester sans SMTP.", file=sys.stderr)
        return 1

    try:
        send_email(msg, cfg)
    except Exception as exc:  # clear log over sophisticated handling
        print(f"ERREUR envoi mail : {exc}", file=sys.stderr)
        return 1

    print(f"Mail envoyé à {msg['To']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
