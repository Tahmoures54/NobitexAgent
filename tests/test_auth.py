# tests/test_auth.py
"""Authentication endpoints: register, login, me, logout."""
from __future__ import annotations

import pytest


# ══════════════════════════════════════════════════════════
# Register
# ══════════════════════════════════════════════════════════
class TestRegister:

    def test_register_success(self, client):
        resp = client.post(
            "/auth/register",
            json={"email": "new@example.com", "password": "secret123"},
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["user"]["email"] == "new@example.com"
        assert data["user"]["plan"] == "free"
        assert data["user"]["is_active"] is True
        assert "password" not in data["user"]
        assert "password_hash" not in data["user"]

    def test_register_duplicate_email(self, client, free_user):
        resp = client.post(
            "/auth/register",
            json={"email": free_user["email"], "password": "another123"},
        )
        assert resp.status_code == 409
        assert "already registered" in resp.json()["detail"].lower()

    def test_register_email_is_normalized(self, client):
        resp = client.post(
            "/auth/register",
            json={"email": "UPPER@Example.COM", "password": "secret123"},
        )
        assert resp.status_code == 201
        assert resp.json()["user"]["email"] == "upper@example.com"

    def test_register_rejects_short_password(self, client):
        resp = client.post(
            "/auth/register",
            json={"email": "short@example.com", "password": "123"},
        )
        assert resp.status_code == 422  # Pydantic validation error

    def test_register_rejects_invalid_email(self, client):
        resp = client.post(
            "/auth/register",
            json={"email": "not-an-email", "password": "secret123"},
        )
        assert resp.status_code == 422

    def test_register_rejects_missing_fields(self, client):
        resp = client.post("/auth/register", json={"email": "a@b.com"})
        assert resp.status_code == 422


# ══════════════════════════════════════════════════════════
# Login
# ══════════════════════════════════════════════════════════
class TestLogin:

    def test_login_success(self, client, free_user):
        resp = client.post(
            "/auth/login",
            json={"email": free_user["email"], "password": free_user["password"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["user"]["email"] == free_user["email"]

    def test_login_wrong_password(self, client, free_user):
        resp = client.post(
            "/auth/login",
            json={"email": free_user["email"], "password": "wrongpassword"},
        )
        assert resp.status_code == 401

    def test_login_unknown_email(self, client):
        resp = client.post(
            "/auth/login",
            json={"email": "nobody@example.com", "password": "whatever123"},
        )
        assert resp.status_code == 401

    def test_login_is_case_insensitive_on_email(self, client, free_user):
        resp = client.post(
            "/auth/login",
            json={
                "email": free_user["email"].upper(),
                "password": free_user["password"],
            },
        )
        assert resp.status_code == 200


# ══════════════════════════════════════════════════════════
# /auth/me
# ══════════════════════════════════════════════════════════
class TestMe:

    def test_me_with_valid_token(self, client, auth_headers, free_user):
        resp = client.get("/auth/me", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["email"] == free_user["email"]

    def test_me_without_token(self, client):
        resp = client.get("/auth/me")
        assert resp.status_code == 401

    def test_me_with_invalid_token(self, client):
        resp = client.get(
            "/auth/me",
            headers={"Authorization": "Bearer not.a.valid.token"},
        )
        assert resp.status_code == 401


# ══════════════════════════════════════════════════════════
# Logout
# ══════════════════════════════════════════════════════════
class TestLogout:

    def test_logout_records_audit(self, client, auth_headers, db_session):
        resp = client.post("/auth/logout", headers=auth_headers)
        assert resp.status_code == 200

        from server.models import AuditLog
        events = db_session.query(AuditLog).filter(AuditLog.event == "logout").all()
        assert len(events) == 1

    def test_logout_requires_auth(self, client):
        resp = client.post("/auth/logout")
        assert resp.status_code == 401


# ══════════════════════════════════════════════════════════
# Audit log coverage
# ══════════════════════════════════════════════════════════
class TestAuditLog:

    def test_login_fail_is_logged(self, client, free_user, db_session):
        client.post(
            "/auth/login",
            json={"email": free_user["email"], "password": "wrong"},
        )
        from server.models import AuditLog
        events = db_session.query(AuditLog).filter(AuditLog.event == "login_fail").all()
        assert len(events) >= 1

    def test_successful_login_is_logged(self, client, free_user, db_session):
        client.post(
            "/auth/login",
            json={"email": free_user["email"], "password": free_user["password"]},
        )
        from server.models import AuditLog
        events = db_session.query(AuditLog).filter(AuditLog.event == "login_ok").all()
        assert len(events) >= 1