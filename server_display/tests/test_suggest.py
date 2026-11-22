"""Tests for GET /api/suggest — the article template picker.

Unlike the chemical inventory this app is forked from, there is nothing to group here:
1 row = 1 article and `code` is UNIQUE, so the endpoint returns a flat list of matching
articles. What it must guarantee is that a template offered for cloning is a *usable*
one — never a soft-deleted article — and that the search text can't turn into a wildcard.
"""
from __future__ import annotations

import sqlite3


def _seed(db_path, rows):
    """Insert articles given as (code, designation, reference, deleted_at) tuples."""
    conn = sqlite3.connect(db_path)
    conn.executemany(
        "INSERT INTO inventory (code, designation, reference, deleted_at) VALUES (?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def _drop_fts(db_path):
    """Remove the FTS index (and the triggers feeding it) to exercise the LIKE fallback."""
    conn = sqlite3.connect(db_path)
    for name in ("inventory_ai", "inventory_au", "inventory_ad"):
        conn.execute(f"DROP TRIGGER IF EXISTS {name}")
    conn.execute("DROP TABLE IF EXISTS inventory_fts")
    conn.commit()
    conn.close()


def _codes(payload):
    return [row["code"] for row in payload["rows"]]


def test_matches_designation_reference_and_code(client_public, temp_db):
    """The three ways an article gets identified at the bench."""
    _seed(temp_db, [("CONS-900", "Gants nitrile taille 7", "SINGNY100/7", None)])

    by_designation = client_public.get("api/suggest", params={"q": "nitrile"}).json()
    by_reference = client_public.get("api/suggest", params={"q": "SINGNY100"}).json()
    by_code = client_public.get("api/suggest", params={"q": "CONS-900"}).json()

    for payload in (by_designation, by_reference, by_code):
        assert "CONS-900" in _codes(payload)


def test_every_word_must_match_somewhere(client_public, temp_db):
    """"gants XL" finds both XL gloves — the words are scattered across the designation.

    The bug this guards: sending the whole phrase as one `%gants XL%` blob matched nothing,
    because no designation contains those two words side by side.
    """
    _seed(temp_db, [
        ("CONS-910", "boite gants en latex taille XL", "112-2753", None),
        ("CONS-911", "boite gants nitrile taille XL", "112-3101", None),
        ("CONS-912", "boite gants en latex taille M", "112-2751", None),
        ("CONS-913", "combinaison VWR XL", "113-0720", None),
    ])

    codes = _codes(client_public.get("api/suggest", params={"q": "gants XL"}).json())

    assert set(codes) == {"CONS-910", "CONS-911"}


def test_every_word_must_match_somewhere_without_fts(client_public, temp_db):
    """Same rule on the LIKE fallback: a missing FTS index must not change the semantics."""
    _seed(temp_db, [
        ("CONS-914", "boite gants en latex taille XL", "", None),
        ("CONS-915", "boite gants en latex taille M", "", None),
    ])
    _drop_fts(temp_db)

    codes = _codes(client_public.get("api/suggest", params={"q": "gants XL"}).json())

    assert codes == ["CONS-914"]


def test_words_may_match_different_columns(client_public, temp_db):
    """One word in the designation, another in the reference — still a match."""
    _seed(temp_db, [("CONS-916", "boite gants nitrile taille XL", "112-3101", None)])

    codes = _codes(client_public.get("api/suggest", params={"q": "nitrile 112-3101"}).json())

    assert "CONS-916" in codes


def test_accents_are_ignored(client_public, temp_db):
    """Typing without accents must find the accented article, and the reverse."""
    _seed(temp_db, [("CONS-917", "paires gants mécanique Fiber Tuf", "", None)])

    assert "CONS-917" in _codes(
        client_public.get("api/suggest", params={"q": "mecanique"}).json()
    )


def test_prefix_match_ranks_first(client_public, temp_db):
    """A designation *starting* with the typed text outranks one merely containing it."""
    _seed(temp_db, [
        ("CONS-901", "Boite de masques FFP2", "", None),
        ("CONS-902", "Masques chirurgicaux", "", None),
    ])

    codes = _codes(client_public.get("api/suggest", params={"q": "masque"}).json())

    # Both are returned; the prefix match comes first. Asserted as a relative order rather
    # than on index 0: the fixture ships its own "Masque FFP3", also a prefix match.
    assert codes.index("CONS-902") < codes.index("CONS-901")


def test_deleted_rows_are_excluded(client_public, temp_db):
    """A soft-deleted article must never be offered as a template."""
    _seed(temp_db, [("CONS-903", "Seringue 10 mL", "", "2026-01-01T00:00:00")])

    payload = client_public.get("api/suggest", params={"q": "seringue"}).json()

    assert _codes(payload) == []


def test_empty_query_returns_latest_first(client_public, temp_db):
    """No query = the most recently created articles, the ones worth declining."""
    _seed(temp_db, [
        ("CONS-904", "Article ancien", "", None),
        ("CONS-905", "Article recent", "", None),
    ])

    payload = client_public.get("api/suggest").json()

    assert _codes(payload)[0] == "CONS-905"


def test_limit_is_honoured_and_capped(client_public, temp_db):
    _seed(temp_db, [(f"CONS-{i:03d}", f"Article {i}", "", None) for i in range(100, 180)])

    assert len(client_public.get("api/suggest", params={"limit": 2}).json()["rows"]) == 2

    capped = client_public.get("api/suggest", params={"limit": 999}).json()
    assert len(capped["rows"]) <= 50


def test_like_wildcards_are_escaped(client_public, temp_db):
    """A bare '%' is a literal percent sign, not 'match everything'."""
    payload = client_public.get("api/suggest", params={"q": "%"}).json()

    assert payload["rows"] == []


def test_underscore_is_literal(client_public, temp_db):
    """'_' must not behave as LIKE's any-single-character wildcard."""
    payload = client_public.get("api/suggest", params={"q": "_"}).json()

    assert payload["rows"] == []


def test_read_password_protects_suggest(client_read_protected, read_auth_header):
    """Same read protection as /api/search — it exposes the same data."""
    assert client_read_protected.get("api/suggest").status_code == 401
    assert client_read_protected.get("api/suggest", headers=read_auth_header).status_code == 200
