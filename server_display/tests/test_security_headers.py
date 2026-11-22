"""Tests for security headers (TDD - RED phase).

These tests verify that security headers are present in responses.
"""
from __future__ import annotations


class TestSecurityHeaders:
    """Tests for HTTP security headers."""

    def test_x_content_type_options(self, client_public):
        """X-Content-Type-Options should be nosniff."""
        resp = client_public.get("/api/search?q=test")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"

    def test_x_frame_options(self, client_public):
        """X-Frame-Options should prevent framing."""
        resp = client_public.get("/api/search?q=test")
        assert resp.headers.get("X-Frame-Options") in ("DENY", "SAMEORIGIN")

    def test_x_xss_protection(self, client_public):
        """X-XSS-Protection should be enabled."""
        resp = client_public.get("/api/search?q=test")
        header = resp.headers.get("X-XSS-Protection", "")
        assert "1" in header
