"""Pytest fixtures for server_display tests."""
from __future__ import annotations

import base64
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def temp_db():
    """Temporary SQLite database with the real schema and a few articles.

    The schema comes from `app.consommables_core.create_schema` rather than a copy pasted
    here: a fixture that drifts from production silently makes every test lie.
    """
    import sqlite3

    from app.consommables_core import create_schema

    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
        db_path = Path(f.name)

    conn = sqlite3.connect(db_path)
    create_schema(conn)

    rows = [
        # code,       designation,        categorie, emplacement, qte, unite,  seuil
        ("CONS-001", "Masque FFP3", "EPI", "Mezzanine", 11, "pièce", 5),
        ("CONS-002", "boite gants en latex taille M", "EPI", "Mezzanine", 33, "boîte", 0),
        ("CONS-003", "Pinces Zinc", "Consommables labo", "Atelier", 0, "pièce", 2),
    ]
    for (code, designation, categorie, emplacement, qte, unite, seuil) in rows:
        # The AFTER INSERT trigger populates inventory_fts automatically.
        conn.execute(
            "INSERT INTO inventory (code, designation, categorie, emplacement, quantite, "
            "unite, seuil_min) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (code, designation, categorie, emplacement, qte, unite, seuil),
        )
        conn.execute(
            "INSERT INTO stock_movement (article_code, delta, quantite_apres, motif) "
            "VALUES (?, ?, ?, 'import')",
            (code, qte, qte),
        )
    conn.commit()
    conn.close()

    yield db_path

    # Cleanup
    db_path.unlink(missing_ok=True)


@pytest.fixture
def client_public(temp_db):
    """Test client with no read password (public access)."""
    from app.config import init_settings
    from app.main import app, limiter

    init_settings(
        db_path=temp_db,
        edit_password="",
        read_password="",
    )

    # Reset rate limiter for test isolation
    limiter.reset()

    with TestClient(app) as client:
        yield client


@pytest.fixture
def client_read_protected(temp_db):
    """Test client with read password set."""
    from app.config import init_settings
    from app.main import app, limiter

    init_settings(
        db_path=temp_db,
        edit_password="editpwd",
        read_password="readpwd",
    )

    # Reset rate limiter for test isolation
    limiter.reset()

    with TestClient(app) as client:
        yield client


@pytest.fixture
def read_auth_header():
    """Valid Basic Auth header for read password 'readpwd'."""
    credentials = base64.b64encode(b":readpwd").decode("utf-8")
    return {"Authorization": f"Basic {credentials}"}


@pytest.fixture
def invalid_auth_header():
    """Invalid Basic Auth header."""
    credentials = base64.b64encode(b":wrongpassword").decode("utf-8")
    return {"Authorization": f"Basic {credentials}"}
