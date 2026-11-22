#!/usr/bin/env python3
"""Import 'Inventaire consomato.xlsx' into SQLite with FTS5 for the consumables inventory.

One-shot destructive load: drops and rebuilds inventory / inventory_fts / edit_log, then
records one `import` movement per article. The xlsx is the source of truth only for the
initial load; afterwards edits happen in the web app.

Columns are mapped by POSITION (1-3: Quantité, DESCRIPTION, Référence) because the xlsx
headers are cp1252 mojibake when read via openpyxl.

Every rule lives in app/consommables_core.py — this script only reads cells and reports.
It prints an audit of the deductions it could not make confidently: run it, read the
report, fix what matters in the web app.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

# Make `app` importable when run as a standalone script from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.consommables_core import (  # noqa: E402
    INVENTORY_COLUMNS,
    create_schema,
    deduce_categorie,
    deduce_fournisseur,
    deduce_unite,
    split_references,
)
from app.utils import CODE_PREFIX  # noqa: E402

# Number of real xlsx columns mapped by position.
N_REAL_COLS = 3

# Container words that, absent an explicit count, leave the unit at "pièce" — either
# because the container IS the product (boîte de Pétri) or because the pack size is
# unknown. Listed in the report so a human can decide.
CONTAINER_WORDS = ("boite", "boîte", "sachet", "carton", "paquet")


def cell_str(v) -> str:
    """Stringify an openpyxl cell value cleanly."""
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def cell_int(v) -> int:
    """Quantities are counts; a blank or unreadable cell means 0, never None."""
    s = cell_str(v)
    try:
        return int(float(s.replace(",", ".")))
    except ValueError:
        return 0


def build_rows(ws) -> list[dict]:
    """Map the sheet to article dicts, applying the deduction rules."""
    rows: list[dict] = []
    for i, raw_row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue  # header (mojibake, ignored — we map by position)
        cells = list(raw_row)
        while len(cells) < N_REAL_COLS:
            cells.append(None)

        designation = cell_str(cells[1])
        if not designation:
            continue  # a row without a description describes nothing

        reference, extra_ref = split_references(cell_str(cells[2]))
        unite, contenu = deduce_unite(designation)

        # Junk columns (index >= 3) and the discarded second reference land in remarque,
        # so the import never silently drops information.
        notes = [cell_str(c) for c in cells[N_REAL_COLS:]]
        notes = [n for n in notes if n]
        if extra_ref:
            notes.insert(0, f"autre référence : {extra_ref}")

        rows.append({
            "code": f"{CODE_PREFIX}{len(rows) + 1:03d}",
            "designation": designation,
            "reference": reference,
            "categorie": deduce_categorie(designation),
            "emplacement": "Mezzanine",
            "quantite": cell_int(cells[0]),
            "unite": unite,
            "contenu_par_unite": contenu,
            "seuil_min": 0,
            "fournisseur": deduce_fournisseur(reference),
            "photo": "",
            "remarque": " | ".join(notes),
        })
    return rows


def insert_rows(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """Insert the articles and their opening `import` movement.

    The opening movement is what makes SUM(delta) == quantite hold from day one, so the
    drift check is meaningful immediately instead of only after the first manual move.
    """
    col_list = ", ".join(f'"{c}"' for c in INVENTORY_COLUMNS)
    placeholders = ", ".join(["?"] * len(INVENTORY_COLUMNS))
    # The AFTER INSERT trigger populates inventory_fts for each row.
    conn.executemany(
        f"INSERT INTO inventory ({col_list}) VALUES ({placeholders})",
        [[r[c] for c in INVENTORY_COLUMNS] for r in rows],
    )
    conn.executemany(
        "INSERT INTO stock_movement (article_code, delta, quantite_apres, motif, user_ip) "
        "VALUES (?, ?, ?, 'import', NULL)",
        [(r["code"], r["quantite"], r["quantite"]) for r in rows],
    )


def print_report(rows: list[dict]) -> None:
    """Print what the rules could not decide — the list to review with a human."""
    print("\n" + "=" * 72)
    print("RAPPORT D'IMPORT")
    print("=" * 72)

    units = Counter(r["unite"] for r in rows)
    print("\nUnités déduites : " + " · ".join(
        f"{n} {u}" for u, n in sorted(units.items(), key=lambda kv: -kv[1])
    ))

    cats = Counter(r["categorie"] or "(vide)" for r in rows)
    print("Catégories      : " + " · ".join(
        f"{n} {c}" for c, n in sorted(cats.items(), key=lambda kv: -kv[1])
    ))

    suspect = [r for r in rows
               if r["contenu_par_unite"] and r["quantite"] > int(r["contenu_par_unite"])]
    if suspect:
        print("\n-- Quantité SUPÉRIEURE au contenu du conditionnement ---------------")
        print("   (le chiffre compte probablement des pièces, pas des paquets)")
        for r in suspect:
            print(f"   {r['code']}  {r['quantite']:>5} {r['unite']} de "
                  f"{r['contenu_par_unite']}  {r['designation'][:48]}")

    to_review = [r for r in rows
                 if r["unite"] == "pièce"
                 and any(w in r["designation"].lower() for w in CONTAINER_WORDS)]
    if to_review:
        print("\n-- Mot-contenant sans compte explicite : laissé en « pièce » -------")
        print("   (à promouvoir en boîte/sachet si c'en est vraiment un)")
        for r in to_review:
            print(f"   {r['code']}  {r['quantite']:>5}        {r['designation'][:56]}")

    no_cat = [r for r in rows if not r["categorie"]]
    if no_cat:
        print("\n-- Sans catégorie --------------------------------------------------")
        for r in no_cat:
            print(f"   {r['code']}  {r['designation'][:62]}")

    no_ref = [r for r in rows if not r["reference"]]
    if no_ref:
        print(f"\n-- Sans référence fournisseur ({len(no_ref)}) --------------------------------")
        for r in no_ref:
            print(f"   {r['code']}  {r['designation'][:62]}")

    dupes = {ref: n for ref, n
             in Counter(r["reference"] for r in rows if r["reference"]).items() if n > 1}
    if dupes:
        print("\n-- Références en double (tolérées : le code CONS fait foi) ----------")
        for ref in dupes:
            for r in rows:
                if r["reference"] == ref:
                    print(f"   {r['code']}  {ref:<16} {r['designation'][:44]}")

    rupture = [r for r in rows if r["quantite"] == 0]
    if rupture:
        print("\n-- En rupture (quantité 0) -----------------------------------------")
        for r in rupture:
            print(f"   {r['code']}  {r['designation'][:62]}")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Import consommables xlsx into SQLite")
    parser.add_argument("--xlsx", default="Inventaire consomato.xlsx")
    parser.add_argument("--sheet", default="inventaire Mezzanine")
    parser.add_argument("--db", default="server_display/consommable_batiment_c.sqlite")
    parser.add_argument(
        "--reset-movements",
        action="store_true",
        help="Also drop stock_movement (default: preserved — it is keyed on the article code "
             "and survives a re-import)",
    )
    args = parser.parse_args()

    xlsx_path = Path(args.xlsx)
    if not xlsx_path.exists():
        raise SystemExit(f"XLSX not found: {xlsx_path}")

    import openpyxl

    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    if args.sheet not in wb.sheetnames:
        raise SystemExit(f"Sheet '{args.sheet}' not found. Sheets: {wb.sheetnames}")
    rows = build_rows(wb[args.sheet])

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        create_schema(conn, drop_movements=args.reset_movements)
        insert_rows(conn, rows)
        conn.commit()
    finally:
        conn.close()

    print(f"OK: {db_path} ({len(rows)} articles)")
    print_report(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
