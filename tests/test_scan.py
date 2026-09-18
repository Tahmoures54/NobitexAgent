# tests/test_scan.py
"""Scanner endpoints: cached read, refresh, quota."""
from __future__ import annotations

import pytest


# ══════════════════════════════════════════════════════════
# Cached read
# ══════════════════════════════════════════════════════════
class TestCachedScan:

    def test_get_scan_returns_cached_rows(self, client, stub_scanner):
        resp = client.get("/api/scan")
        assert resp.status_code == 200
        data = resp.json()
        assert "rows" in data
        assert "count" in data
        assert "updated_at" in data
        assert data["count"] == 3
        assert data["rows"][0]["Symbol"] == "BTC"

    def test_get_scan_is_public(self, client, stub_scanner):
        """No auth required to view the cached scan."""
        resp = client.get("/api/scan")
        assert resp.status_code == 200

    def test_get_scan_empty_cache(self, client):
        """Without a stub, the cache should be empty but not error."""
        resp = client.get("/api/scan")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0


# ══════════════════════════════════════════════════════════
# Refresh (guest)
# ══════════════════════════════════════════════════════════
class TestRefreshGuest:

    def test_guest_refresh_returns_rows(self, client, stub_scanner):
        resp = client.post("/api/scan/refresh")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["count"] == 3
        # Remaining decrements from 3 → 2
        assert data["remaining_today"] == 2

    def test_guest_quota_exhausts(self, client, stub_scanner):
        # Guest limit is 3 per IP per day
        for i in range(3):
            r = client.post("/api/scan/refresh")
            assert r.status_code == 200, f"attempt {i}: {r.text}"

        # Fourth attempt should be blocked
        r = client.post("/api/scan/refresh")
        assert r.status_code == 429
        assert "guest" in r.json()["detail"].lower()


# ══════════════════════════════════════════════════════════
# Refresh (free user)
# ══════════════════════════════════════════════════════════
class TestRefreshFreeUser:

    def test_free_user_can_refresh(self, client, stub_scanner, auth_headers):
        resp = client.post("/api/scan/refresh", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["remaining_today"] == 2  # 3 - 1

    def test_free_user_quota(self, client, stub_scanner, auth_headers):
        # Burn the free quota (3 scans)
        for i in range(3):
            r = client.post("/api/scan/refresh", headers=auth_headers)
            assert r.status_code == 200, f"attempt {i}: {r.text}"

        # Fourth should be 429
        r = client.post("/api/scan/refresh", headers=auth_headers)
        assert r.status_code == 429
        assert "limit" in r.json()["detail"].lower()

    def test_free_user_status_reports_remaining(
        self, client, stub_scanner, auth_headers
    ):
        client.post("/api/scan/refresh", headers=auth_headers)
        resp = client.get("/api/scan/status", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["authenticated"] is True
        assert data["plan"] == "free"
        assert data["unlimited"] is False
        assert data["remaining_today"] == 2


# ══════════════════════════════════════════════════════════
# Refresh (Pro user)
# ══════════════════════════════════════════════════════════
class TestRefreshProUser:

    def test_pro_user_unlimited(self, client, stub_scanner, pro_headers):
        # Five refreshes stay within the route's 5/minute transport rate limit.
        # Pro has no daily quota; transport throttling still applies.
        for i in range(5):
            r = client.post("/api/scan/refresh", headers=pro_headers)
            assert r.status_code == 200, f"attempt {i}: {r.text}"
            assert r.json()["remaining_today"] == -1  # unlimited marker

    def test_pro_status_reports_unlimited(
        self, client, stub_scanner, pro_headers
    ):
        resp = client.get("/api/scan/status", headers=pro_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["authenticated"] is True
        assert data["plan"] == "pro"
        assert data["unlimited"] is True
        assert data["remaining_today"] == -1


# ══════════════════════════════════════════════════════════
# Scan status (guest)
# ══════════════════════════════════════════════════════════
class TestScanStatusGuest:

    def test_guest_status(self, client, stub_scanner):
        resp = client.get("/api/scan/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["authenticated"] is False
        assert data["unlimited"] is False
        assert data["remaining_today"] == 3


# ══════════════════════════════════════════════════════════
# Rate limit (not quota, actual RPS limit)
# ══════════════════════════════════════════════════════════
class TestRateLimit:

    def test_rapid_refresh_is_rate_limited(self, client, stub_scanner, pro_headers):
        """
        `@limiter.limit("5/minute")` on /api/scan/refresh.

        Note: slowapi uses an in-memory store keyed by remote address.
        In tests, all requests share the same key, so we can trigger 429.
        """
        codes = []
        for _ in range(8):
            r = client.post("/api/scan/refresh", headers=pro_headers)
            codes.append(r.status_code)

        # At least one of the later calls must be 429
        assert 429 in codes, f"expected rate limit, got {codes}"