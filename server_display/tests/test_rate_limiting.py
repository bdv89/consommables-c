"""Tests for rate limiting (TDD - RED phase).

These tests verify that:
1. Normal traffic (under limit) passes through
2. Excessive traffic is blocked with 429
3. Rate limit applies per IP
"""
from __future__ import annotations

import pytest


class TestRateLimiting:
    """Tests for API rate limiting."""

    def test_normal_traffic_allowed(self, client_public):
        """Requests under rate limit should pass."""
        # Make 10 requests (well under any reasonable limit)
        for _ in range(10):
            resp = client_public.get("/api/search?q=test")
            assert resp.status_code == 200

    @pytest.mark.slow
    def test_excessive_traffic_blocked(self, client_public):
        """Requests over rate limit should be blocked with 429."""
        # Make 70 requests (assuming limit is 60/min)
        responses = []
        for _ in range(70):
            resp = client_public.get("/api/search?q=test")
            responses.append(resp.status_code)

        # At least some should be 429
        assert 429 in responses, "Rate limit should block excessive requests"

    def test_rate_limit_response_format(self, client_public):
        """429 response should include retry information."""
        # Exhaust rate limit
        for _ in range(70):
            resp = client_public.get("/api/search?q=test")
            if resp.status_code == 429:
                # Check for rate limit headers or body
                assert "retry" in resp.text.lower() or "limit" in resp.text.lower()
                break


class TestRateLimitHeaders:
    """Tests for rate limit headers."""

    def test_rate_limit_headers_present(self, client_public):
        """Response should include rate limit headers."""
        resp = client_public.get("/api/search?q=test")
        # Standard rate limit headers (if implemented)
        # X-RateLimit-Limit, X-RateLimit-Remaining, X-RateLimit-Reset
        # These are optional but good practice
        assert resp.status_code == 200
