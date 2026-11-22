"""Tests for the daily low-stock e-mail digest."""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

# scripts/ is not a package (the scripts are run by path, not imported), so load the
# module from its file rather than adding an __init__.py just for the tests.
_spec = importlib.util.spec_from_file_location(
    "send_low_stock_email",
    Path(__file__).resolve().parents[1] / "scripts" / "send_low_stock_email.py",
)
alert = importlib.util.module_from_spec(_spec)
# Register before exec: @dataclass resolves its own module through sys.modules.
sys.modules[_spec.name] = alert
_spec.loader.exec_module(alert)


@pytest.fixture
def conn():
    from app.consommables_core import create_schema

    c = sqlite3.connect(":memory:")
    create_schema(c)
    for code, designation, qte, seuil in [
        ("CONS-001", "Masque FFP3", 3, 5),
        ("CONS-002", "Pinces Zinc", 0, 0),      # no threshold -> silent even at zero
        ("CONS-003", "Gants latex M", 33, 10),  # comfortable
        ("CONS-004", "Stylo diamant", 0, 1),    # rupture
    ]:
        c.execute(
            "INSERT INTO inventory (code, designation, quantite, seuil_min) VALUES (?, ?, ?, ?)",
            (code, designation, qte, seuil),
        )
    c.commit()
    yield c
    c.close()


def test_load_below_articles_uses_the_same_predicate_as_the_app(conn):
    below = alert.load_below_articles(conn)
    codes = [a["code"] for a in below]

    assert "CONS-001" in codes
    assert "CONS-004" in codes
    assert "CONS-003" not in codes          # above threshold
    assert "CONS-002" not in codes          # seuil_min = 0 means alerting disabled


def test_below_articles_are_sorted_by_shortfall(conn):
    below = alert.load_below_articles(conn)
    # CONS-001 misses 2, CONS-004 misses 1 — the worst shortfall leads.
    assert [a["code"] for a in below] == ["CONS-001", "CONS-004"]


def test_email_body_names_the_article_the_count_and_the_threshold(conn):
    body = alert.build_email_body(alert.load_below_articles(conn))

    assert "Masque FFP3" in body
    assert "CONS-001" in body
    assert "3 en stock / seuil 5" in body
    assert "RUPTURE" in body               # CONS-004 is at zero
    assert "Pinces Zinc" not in body       # no threshold -> never mentioned


def test_no_low_stock_produces_an_all_clear_body():
    body = alert.build_email_body([])
    assert "Aucun article sous le seuil" in body


def test_subject_states_the_count(conn):
    below = alert.load_below_articles(conn)
    msg = alert.build_message(below, "a@b.c", ["d@e.f"])
    assert msg["Subject"] == "[Consommables Bât. C] 2 article(s) sous le seuil"

    ok = alert.build_message([], "a@b.c", ["d@e.f"])
    assert ok["Subject"] == "[Consommables Bât. C] Stock OK"
