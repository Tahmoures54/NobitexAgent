"""Passwordless authenticator authentication tests."""
from __future__ import annotations

import pyotp


class TestRegister:
    def test_register_returns_authenticator_setup(self, client):
        payload = {
            "email": "new@example.com",
            "full_name": "New User",
            "phone_number": "+989121234567",
        }
        resp = client.post("/auth/register", json=payload)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["secret"]
        assert data["otpauth_uri"].startswith("otpauth://totp/")
        assert data["qr_code_data_uri"].startswith("data:image/png;base64,")

    def test_register_duplicate_email(self, client, free_user):
        resp = client.post(
            "/auth/register",
            json={
                "email": free_user["email"],
                "full_name": "Another User",
                "phone_number": "+989121234568",
            },
        )
        assert resp.status_code == 409

    def test_register_requires_name_and_phone(self, client):
        resp = client.post("/auth/register", json={"email": "a@example.com"})
        assert resp.status_code == 422

    def test_register_rejects_invalid_phone(self, client):
        resp = client.post(
            "/auth/register",
            json={
                "email": "a@example.com",
                "full_name": "Test User",
                "phone_number": "09121234567",
            },
        )
        assert resp.status_code == 422


class TestAuthenticatorActivation:
    def test_setup_code_activates_account(self, client):
        payload = {
            "email": "activate@example.com",
            "full_name": "Activate User",
            "phone_number": "+989121234570",
        }
        setup = client.post("/auth/register", json=payload).json()
        code = pyotp.TOTP(setup["secret"]).now()
        resp = client.post(
            "/auth/setup/verify",
            json={"email": payload["email"], "code": code},
        )
        assert resp.status_code == 200, resp.text
        assert "access_token" in resp.json()
        assert resp.json()["user"]["email"] == payload["email"]

    def test_wrong_setup_code_is_rejected(self, client):
        payload = {
            "email": "wrongsetup@example.com",
            "full_name": "Wrong Setup",
            "phone_number": "+989121234571",
        }
        client.post("/auth/register", json=payload)
        resp = client.post(
            "/auth/setup/verify",
            json={"email": payload["email"], "code": "000000"},
        )
        assert resp.status_code == 401


class TestLogin:
    def test_login_with_authenticator_code(self, client, free_user, db_session):
        from server.models import User
        user = db_session.query(User).filter(User.email == free_user["email"]).one()
        from server.auth import decrypt_totp_secret
        secret = decrypt_totp_secret(user.totp_secret_encrypted)
        code = pyotp.TOTP(secret).now()
        resp = client.post(
            "/auth/login",
            json={"email": free_user["email"], "code": code},
        )
        assert resp.status_code == 200
        assert resp.json()["user"]["email"] == free_user["email"]

    def test_login_wrong_code(self, client, free_user):
        resp = client.post(
            "/auth/login",
            json={"email": free_user["email"], "code": "000000"},
        )
        assert resp.status_code == 401

    def test_login_unknown_email(self, client):
        resp = client.post(
            "/auth/login",
            json={"email": "nobody@example.com", "code": "123456"},
        )
        assert resp.status_code == 401


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


class TestAuditLog:
    def test_login_fail_is_logged(self, client, free_user, db_session):
        client.post(
            "/auth/login",
            json={"email": free_user["email"], "code": "000000"},
        )
        from server.models import AuditLog
        events = db_session.query(AuditLog).filter(AuditLog.event == "login_fail").all()
        assert len(events) >= 1

    def test_successful_login_is_logged(self, client, free_user, db_session):
        from server.models import User
        user = db_session.query(User).filter(User.email == free_user["email"]).one()
        from server.auth import decrypt_totp_secret
        secret = decrypt_totp_secret(user.totp_secret_encrypted)
        code = pyotp.TOTP(secret).now()
        client.post(
            "/auth/login",
            json={"email": free_user["email"], "code": code},
        )
        from server.models import AuditLog
        events = db_session.query(AuditLog).filter(AuditLog.event == "login_ok").all()
        assert len(events) >= 1
