"""Tests for the consumables inventory: deduction rules, codes, movements, low stock."""
from __future__ import annotations

import sqlite3

import pytest

from app.consommables_core import (
    apply_movement,
    deduce_categorie,
    deduce_fournisseur,
    deduce_unite,
    low_stock_rows,
    split_references,
)
from app.utils import next_code


# --------------------------------------------------------------------------- units


@pytest.mark.parametrize(
    "designation, unite, contenu",
    [
        # Explicit packaging: "<container> de <N>" is the only form we trust.
        ("sachets de 10 masque chirugicale", "sachet", "10"),
        ("sachet de 100 Sacs à fermeture zip 40x60 mm", "sachet", "100"),
        ("boite de 50 tampon buvard", "boîte", "50"),
        ("boites de 100 lamelles couvre-objet microscope", "boîte", "100"),
        ("paquet de 100 coton tige pour rainure", "paquet", "100"),
        ("Film plastique villeda 30cmx300m (Boite de 3)", "boîte", "3"),
        # Pairs and rolls are units in their own right.
        ("paires de gant proctection chaleur (Temp dex 710 ) Taille 7", "paire", ""),
        ("paire de gants anti coupures ( Krytech 586 ) taille 6", "paire", ""),
        ("rouleau d'aluminium 300mmX0,030X10m", "rouleau", ""),
        ("Rouleau de papier bleu 22X36cm", "rouleau", ""),
        ("Carton de charlotte rouge", "carton", ""),
        # Plain items.
        ("Masque FFP3", "pièce", ""),
        ("Pissettes pour eau déionisée 500 ml (Azlon®)", "pièce", ""),
        ("combinaison VWR XL", "pièce", ""),
    ],
)
def test_deduce_unite(designation, unite, contenu):
    assert deduce_unite(designation) == (unite, contenu)


@pytest.mark.parametrize(
    "designation",
    [
        # THE trap: the container word is part of the PRODUCT NAME, not the packaging.
        # A naive \bboite\b rule turns 1000 Petri dishes into 1000 boxes.
        "petite boite pétri ronde 55mm",
        "grande boite pétri carré",
        "boite à pizza",
        "sachet papier blanc (salle propre) tx 1109",
    ],
)
def test_deduce_unite_container_word_in_product_name_stays_piece(designation):
    assert deduce_unite(designation) == ("pièce", "")


# ---------------------------------------------------------------------- categories


@pytest.mark.parametrize(
    "designation, categorie",
    [
        ("boite gants en latex taille S", "EPI"),
        ("Masque FFP3", "EPI"),
        ("Lunette de sécurité - Amsterdam", "EPI"),
        ("combinaison kleenguard produit chimique taille M", "EPI"),
        ("Bouteilles en verre avec ou sans bouchons à vis 250 ml", "Verrerie"),
        ("Pissettes pour Isopropanol 500 ml", "Verrerie"),
        ("seringues Henke-Ject 10ml", "Seringues"),
        ("aiguilles Neolus 25mm", "Seringues"),
        ("sachet de 100 Sacs à fermeture zip 40x60 mm", "Emballage"),
        ("kapton fins", "Adhésifs"),
        ("rouleau de scotch professional sensitive", "Adhésifs"),
        ("Eponge vitre Spontex Azella 82 viscose 120x80x35mm", "Nettoyage"),
        ("Stylo Pointe à Diamant - Manche en bois", "Outillage"),
        ("rouleau d'aluminium 300mmX150m", "Emballage"),
        ("sachet petis pots pour pesée blanc et hexagonale", "Consommables labo"),
        ("papier ph 4,8m", "Consommables labo"),
    ],
)
def test_deduce_categorie(designation, categorie):
    assert deduce_categorie(designation) == categorie


def test_deduce_categorie_petri_is_labware_not_packaging():
    """"boite pétri" contains "boite" but is labware — it must not land in Emballage."""
    assert deduce_categorie("grande boite pétri ronde 90mm") == "Consommables labo"


# ----------------------------------------------------------------- refs / supplier


def test_deduce_fournisseur_from_vwr_reference_shape():
    assert deduce_fournisseur("111-1022") == "VWR"
    assert deduce_fournisseur("112-2750") == "VWR"
    assert deduce_fournisseur("PMPC806-WC") == ""
    assert deduce_fournisseur("") == ""


def test_split_references_keeps_first_and_returns_the_rest():
    assert split_references("112-5101 ---> MAPA34710091") == ("112-5101", "MAPA34710091")
    assert split_references("825-14AM / 817-0055") == ("825-14AM", "817-0055")
    assert split_references("111-1022") == ("111-1022", "")
    assert split_references("") == ("", "")


@pytest.mark.parametrize(
    "raw",
    [
        # A slash with no spaces is part of the reference: /7 /8 /9 /10 are glove sizes.
        # Splitting them collapsed four articles onto one duplicated reference.
        "SINGNY100/7",
        "SINGNY100/10",
        # A lone dash is not a separator either.
        "8833 - 111-0451",
    ],
)
def test_split_references_leaves_single_references_whole(raw):
    assert split_references(raw) == (raw, "")


# ----------------------------------------------------------------------- codes


def test_next_code_max_plus_one_padded():
    assert next_code([]) == "CONS-001"
    assert next_code(["CONS-001", "CONS-002"]) == "CONS-003"
    assert next_code(["CONS-001", "CONS-087"]) == "CONS-088"
    # max+1, never count+1: a deleted article must not free its code for reuse.
    assert next_code(["CONS-001", "CONS-009"]) == "CONS-010"
    # Non-conforming values are ignored, not crashed on.
    assert next_code(["CONS-001", "", None, "112-2750"]) == "CONS-002"


# -------------------------------------------------------------------- movements


@pytest.fixture
def conn():
    """In-memory DB with the real schema and two articles."""
    from app.consommables_core import create_schema

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    create_schema(c)
    c.execute(
        "INSERT INTO inventory (code, designation, quantite, seuil_min) VALUES (?, ?, ?, ?)",
        ("CONS-001", "Masque FFP3", 11, 5),
    )
    c.execute(
        "INSERT INTO inventory (code, designation, quantite, seuil_min) VALUES (?, ?, ?, ?)",
        ("CONS-002", "Pinces Zinc", 0, 0),
    )
    c.commit()
    yield c
    c.close()


def test_movement_updates_quantity_and_writes_journal_together(conn):
    apply_movement(conn, "CONS-001", -2, "sortie", "10.0.0.1")

    assert conn.execute("SELECT quantite FROM inventory WHERE code='CONS-001'").fetchone()[0] == 9
    mvt = conn.execute(
        "SELECT * FROM stock_movement WHERE article_code='CONS-001' ORDER BY id DESC"
    ).fetchone()
    assert mvt["delta"] == -2
    assert mvt["quantite_apres"] == 9
    assert mvt["motif"] == "sortie"
    assert mvt["user_ip"] == "10.0.0.1"


def test_movement_refuses_negative_stock_and_leaves_nothing_behind(conn):
    with pytest.raises(ValueError):
        apply_movement(conn, "CONS-002", -1, "sortie", None)

    # Neither the quantity nor the journal may have moved.
    assert conn.execute("SELECT quantite FROM inventory WHERE code='CONS-002'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM stock_movement").fetchone()[0] == 0


def test_movement_on_unknown_article_is_refused(conn):
    with pytest.raises(ValueError):
        apply_movement(conn, "CONS-999", 1, "reappro", None)
    assert conn.execute("SELECT COUNT(*) FROM stock_movement").fetchone()[0] == 0


def test_quantity_never_drifts_from_the_journal(conn):
    for delta in (-1, -1, +50, -3, +2):
        apply_movement(conn, "CONS-001", delta, "test", None)

    drift = conn.execute(
        "SELECT code FROM inventory i WHERE quantite != "
        "(SELECT COALESCE(SUM(delta), 0) + 11 FROM stock_movement WHERE article_code = i.code) "
        "AND code = 'CONS-001'"
    ).fetchall()
    assert drift == []
    assert conn.execute("SELECT quantite FROM inventory WHERE code='CONS-001'").fetchone()[0] == 58


def test_movement_bumps_version_for_optimistic_locking(conn):
    before = conn.execute("SELECT version FROM inventory WHERE code='CONS-001'").fetchone()[0]
    apply_movement(conn, "CONS-001", +1, "reappro", None)
    after = conn.execute("SELECT version FROM inventory WHERE code='CONS-001'").fetchone()[0]
    assert after == before + 1


# -------------------------------------------------------------------- low stock


def test_movements_api_applies_and_journals(client_public):
    r = client_public.post("/api/movements", json={"code": "CONS-001", "delta": -2}).json()
    assert r["success"] is True
    assert r["quantite"] == 9
    assert r["motif"] == "sortie"          # negative delta defaults to a withdrawal

    r = client_public.post("/api/movements", json={"code": "CONS-001", "delta": 5}).json()
    assert r["quantite"] == 14
    assert r["motif"] == "reappro"         # positive delta defaults to a restock

    journal = client_public.get("/api/movements?code=CONS-001").json()
    assert journal["total"] == 3           # opening import + the two moves
    assert journal["rows"][0]["quantite_apres"] == 14
    assert journal["rows"][0]["designation"] == "Masque FFP3"


def test_movements_api_refuses_to_go_negative(client_public):
    r = client_public.post("/api/movements", json={"code": "CONS-003", "delta": -1}).json()
    assert r["success"] is False
    assert "insuffisant" in r["error"].lower()

    # Nothing recorded: the journal must not describe a movement that did not happen.
    assert client_public.get("/api/movements?code=CONS-003").json()["total"] == 1


def test_movements_api_rejects_unknown_article_and_zero_delta(client_public):
    assert client_public.post(
        "/api/movements", json={"code": "CONS-999", "delta": 1}
    ).json()["success"] is False
    assert client_public.post(
        "/api/movements", json={"code": "CONS-001", "delta": 0}
    ).json()["success"] is False


def test_set_quantity_records_the_difference_as_a_correction(client_public):
    r = client_public.post(
        "/api/movements/set", json={"code": "CONS-001", "quantite": 20}
    ).json()
    assert r["success"] is True
    assert (r["quantite"], r["delta"], r["motif"]) == (20, 9, "correction")


def test_put_inventory_cannot_change_quantity(client_public):
    """The only write path for `quantite` is the movements API — else the journal drifts."""
    client_public.put("/api/inventory/1", json={"quantite": 999, "remarque": "test"})

    row = client_public.get("/api/inventory/1").json()["row"]
    assert row["quantite"] == 11        # untouched
    assert row["remarque"] == "test"    # the rest of the update went through


def test_search_counters_report_shortage_not_wear(client_public):
    data = client_public.get("/api/search").json()
    assert data["total_all"] == 3
    assert data["count_rupture"] == 1      # CONS-003 Pinces Zinc at 0
    assert data["count_sous_seuil"] == 1   # CONS-003 has seuil_min = 2
    assert "count_neuve" not in data       # wear states are gone


def test_search_routes_a_known_category_to_its_column(client_public):
    data = client_public.get("/api/search?q=EPI").json()
    assert data["total"] == 2              # the two EPI articles, not a loose prefix match


def test_create_article_assigns_the_next_code_and_opens_the_journal(client_public):
    r = client_public.post(
        "/api/inventory", json={"designation": "Nouveau", "quantite": 7}
    ).json()
    assert r["success"] is True
    assert r["code"] == "CONS-004"

    journal = client_public.get("/api/movements?code=CONS-004").json()
    assert journal["total"] == 1
    assert journal["rows"][0]["quantite_apres"] == 7


def test_low_stock_only_flags_articles_with_a_real_threshold():
    rows = [
        {"code": "CONS-001", "designation": "Masque", "quantite": 3, "seuil_min": 5},
        {"code": "CONS-002", "designation": "Pinces", "quantite": 0, "seuil_min": 0},
        {"code": "CONS-003", "designation": "Gants", "quantite": 5, "seuil_min": 5},
        {"code": "CONS-004", "designation": "Sacs", "quantite": 800, "seuil_min": 100},
    ]
    low = low_stock_rows(rows)

    codes = [r["code"] for r in low]
    assert codes == ["CONS-001"]
    # seuil_min = 0 means "disabled" — a zero-stock article without a threshold stays silent.
    assert "CONS-002" not in codes
    # Strict predicate: quantite == seuil_min is NOT below.
    assert "CONS-003" not in codes
