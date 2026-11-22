"""Tests for optional read authentication (TDD - RED phase).

These tests verify that:
1. Without --read-password, all read endpoints are public
2. With --read-password, read endpoints require authentication
3. Valid credentials allow access
4. Invalid credentials are rejected
"""
from __future__ import annotations


class TestPublicReadAccess:
    """Tests when no read password is configured (public access)."""

    def test_search_public_when_no_password(self, client_public):
        """Search endpoint is accessible without auth."""
        resp = client_public.get("/api/search?q=ethanol")
        assert resp.status_code == 200

    def test_columns_public_when_no_password(self, client_public):
        """Columns endpoint is accessible without auth."""
        resp = client_public.get("/api/columns")
        assert resp.status_code == 200

    def test_export_public_when_no_password(self, client_public):
        """Export endpoint is accessible without auth."""
        resp = client_public.get("/api/export?q=ethanol")
        assert resp.status_code == 200


class TestProtectedReadAccess:
    """Tests when read password is configured."""

    def test_search_requires_auth_when_password_set(self, client_read_protected):
        """Search endpoint returns 401 without auth."""
        resp = client_read_protected.get("/api/search?q=ethanol")
        assert resp.status_code == 401
        assert "WWW-Authenticate" in resp.headers

    def test_columns_requires_auth_when_password_set(self, client_read_protected):
        """Columns endpoint returns 401 without auth."""
        resp = client_read_protected.get("/api/columns")
        assert resp.status_code == 401

    def test_export_requires_auth_when_password_set(self, client_read_protected):
        """Export endpoint returns 401 without auth."""
        resp = client_read_protected.get("/api/export?q=ethanol")
        assert resp.status_code == 401

    def test_search_works_with_valid_auth(
        self, client_read_protected, read_auth_header
    ):
        """Search endpoint accessible with valid credentials."""
        resp = client_read_protected.get("/api/search?q=ethanol", headers=read_auth_header)
        assert resp.status_code == 200

    def test_search_rejects_invalid_auth(
        self, client_read_protected, invalid_auth_header
    ):
        """Search endpoint rejects invalid credentials."""
        resp = client_read_protected.get(
            "/api/search?q=ethanol", headers=invalid_auth_header
        )
        assert resp.status_code == 401


class TestMediaFileAccess:
    """Tests for media (article photo) access control."""

    def test_media_public_when_no_password(self, client_public):
        """Media files accessible without auth (even if they don't exist)."""
        resp = client_public.get("/media/photos/test.jpg")
        # 404 is OK - means auth passed but file not found
        assert resp.status_code in (200, 404)

    def test_media_requires_auth_when_password_set(self, client_read_protected):
        """Media files return 401 without auth."""
        resp = client_read_protected.get("/media/photos/test.jpg")
        assert resp.status_code == 401

    def test_media_rejects_path_traversal(self, client_public):
        """A path escaping media_root is refused, not served."""
        resp = client_public.get("/media/../../CLAUDE.md")
        assert resp.status_code in (400, 404)
