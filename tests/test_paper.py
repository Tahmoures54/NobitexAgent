"""Paper trading integration tests for sizing, SL/TP and trailing stops."""
from __future__ import annotations

import pyotp
import pytest

from server.models import User
from server.services import paper as paper_svc


def _open_payload(**overrides) -> dict:
    base = {
        "symbol": "BTC",
        "side": "long",
        "price": 50000.0,
        "size_usd": 1000.0,
        "stop_loss_pct": 3.0,
        "take_profit_pct": 50.0,
    }
    base.update(overrides)
    return base


def _register_user(client, email: str, phone: str) -> dict:
    payload = {
        "email": email,
        "full_name": "Test User",
        "phone_number": phone,
    }
    setup = client.post("/auth/register", json=payload)
    assert setup.status_code == 200, setup.text
    code = pyotp.TOTP(setup.json()["secret"]).now()
    activated = client.post(
        "/auth/setup/verify",
        json={"email": email, "code": code},
    )
    assert activated.status_code == 200, activated.text
    return activated.json()


class TestPaperAuthGuard:
    def test_open_requires_auth(self, client):
        assert client.post("/api/paper/open", json=_open_payload()).status_code == 401

    def test_list_requires_auth(self, client):
        assert client.get("/api/paper/list").status_code == 401

    def test_stats_requires_auth(self, client):
        assert client.get("/api/paper/stats").status_code == 401


class TestOpenTrade:
    def test_open_uses_90_percent_virtual_cash(self, client, auth_headers):
        r = client.post("/api/paper/open", json=_open_payload(), headers=auth_headers)
        assert r.status_code == 201, r.text
        t = r.json()
        assert t["status"] == "open"
        assert t["notional"] == pytest.approx(900.0)
        assert t["position_size"] == pytest.approx(900.0 / 50000.0)
        assert t["stop_loss"] == pytest.approx(48500.0)
        assert t["take_profit"] == pytest.approx(75000.0)

    def test_requested_size_does_not_override_strategy_sizing(self, client, auth_headers):
        a = client.post(
            "/api/paper/open",
            json=_open_payload(size_usd=1.0),
            headers=auth_headers,
        )
        assert a.status_code == 201
        assert a.json()["notional"] == pytest.approx(900.0)

    def test_open_short_trade(self, client, auth_headers):
        r = client.post(
            "/api/paper/open",
            json=_open_payload(side="short"),
            headers=auth_headers,
        )
        assert r.status_code == 201
        t = r.json()
        assert t["side"] == "short"
        assert t["stop_loss"] == pytest.approx(51500.0)
        assert t["take_profit"] == pytest.approx(25000.0)

    def test_open_rejects_invalid_price(self, client, auth_headers):
        assert client.post(
            "/api/paper/open", json=_open_payload(price=0), headers=auth_headers
        ).status_code == 422


class TestCloseTrade:
    def test_close_long_profit(self, client, auth_headers):
        opened = client.post("/api/paper/open", json=_open_payload(), headers=auth_headers).json()
        closed = client.post(
            f"/api/paper/{opened['id']}/close",
            json={"price": 53000.0, "reason": "Manual"},
            headers=auth_headers,
        )
        assert closed.status_code == 200
        t = closed.json()
        assert t["pnl_pct"] == pytest.approx(6.0)
        assert t["pnl_usd"] == pytest.approx(54.0)

    def test_close_short_profit(self, client, auth_headers):
        opened = client.post(
            "/api/paper/open", json=_open_payload(side="short"), headers=auth_headers
        ).json()
        closed = client.post(
            f"/api/paper/{opened['id']}/close",
            json={"price": 47000.0, "reason": "Take Profit"},
            headers=auth_headers,
        )
        assert closed.status_code == 200
        assert closed.json()["pnl_pct"] == pytest.approx(6.0)
        assert closed.json()["pnl_usd"] == pytest.approx(54.0)

    def test_close_already_closed(self, client, auth_headers):
        opened = client.post("/api/paper/open", json=_open_payload(), headers=auth_headers).json()
        path = f"/api/paper/{opened['id']}/close"
        assert client.post(path, json={"price": 51000.0}, headers=auth_headers).status_code == 200
        assert client.post(path, json={"price": 52000.0}, headers=auth_headers).status_code == 404


class TestUserIsolation:
    def test_user_cannot_see_or_close_other_users_trade(self, client, auth_headers):
        alice_trade = client.post(
            "/api/paper/open", json=_open_payload(), headers=auth_headers
        ).json()
        bob = _register_user(client, "bob@example.com", "+989121234501")
        bob_headers = {"Authorization": f"Bearer {bob['access_token']}"}

        assert client.get("/api/paper/list", headers=bob_headers).json() == []
        assert client.get("/api/paper/stats", headers=bob_headers).json()["total_trades"] == 0

        r = client.post(
            f"/api/paper/{alice_trade['id']}/close",
            json={"price": 49000.0},
            headers=bob_headers,
        )
        assert r.status_code == 404


class TestAutoSLTP:
    def test_auto_close_take_profit(self, client, auth_headers, db_session, free_user):
        client.post(
            "/api/paper/open",
            json=_open_payload(take_profit_pct=6.0),
            headers=auth_headers,
        )
        user = db_session.query(User).filter(User.email == free_user["email"]).one()
        assert paper_svc.check_exits(db_session, user.id, {"BTC": 54000.0}) == 1
        trade = client.get("/api/paper/list", headers=auth_headers).json()[0]
        assert trade["exit_reason"] == "Take Profit"
        assert trade["exit_price"] == pytest.approx(53000.0)

    def test_auto_close_stop_loss(self, client, auth_headers, db_session, free_user):
        client.post(
            "/api/paper/open", json=_open_payload(), headers=auth_headers
        )
        user = db_session.query(User).filter(User.email == free_user["email"]).one()
        assert paper_svc.check_exits(db_session, user.id, {"BTC": 48000.0}) == 1
        trade = client.get("/api/paper/list", headers=auth_headers).json()[0]
        assert trade["exit_reason"] == "Stop Loss"
        assert trade["exit_price"] == pytest.approx(48500.0)

    def test_no_auto_close_within_range(self, client, auth_headers, db_session, free_user):
        client.post("/api/paper/open", json=_open_payload(), headers=auth_headers)
        user = db_session.query(User).filter(User.email == free_user["email"]).one()
        assert paper_svc.check_exits(db_session, user.id, {"BTC": 50500.0}) == 0
        assert client.get("/api/paper/list", headers=auth_headers).json()[0]["status"] == "open"


class TestTrailingStop:
    def test_long_trailing_activates_and_moves_up(self, client, auth_headers, db_session, free_user):
        opened = client.post(
            "/api/paper/open", json=_open_payload(), headers=auth_headers
        ).json()
        user = db_session.query(User).filter(User.email == free_user["email"]).one()

        assert paper_svc.check_exits(db_session, user.id, {"BTC": 52000.0}) == 0
        t = client.get("/api/paper/list", headers=auth_headers).json()[0]
        assert t["trailing_active"] is True
        assert t["highest_price"] == pytest.approx(52000.0)
        assert t["trailing_stop"] == pytest.approx(50440.0)
        assert t["stop_loss"] == pytest.approx(50440.0)

        assert paper_svc.check_exits(db_session, user.id, {"BTC": 55000.0}) == 0
        t = client.get("/api/paper/list", headers=auth_headers).json()[0]
        assert t["highest_price"] == pytest.approx(55000.0)
        assert t["trailing_stop"] == pytest.approx(53350.0)

    def test_long_trailing_closes_on_pullback(self, client, auth_headers, db_session, free_user):
        client.post("/api/paper/open", json=_open_payload(), headers=auth_headers)
        user = db_session.query(User).filter(User.email == free_user["email"]).one()

        assert paper_svc.check_exits(db_session, user.id, {"BTC": 55000.0}) == 0
        assert paper_svc.check_exits(db_session, user.id, {"BTC": 53000.0}) == 1
        t = client.get("/api/paper/list", headers=auth_headers).json()[0]
        assert t["status"] == "closed"
        assert t["exit_reason"] == "Trailing Stop"
        assert t["exit_price"] == pytest.approx(53350.0)

    def test_short_trailing_activates_and_moves_down(self, client, auth_headers, db_session, free_user):
        client.post(
            "/api/paper/open",
            json=_open_payload(side="short"),
            headers=auth_headers,
        )
        user = db_session.query(User).filter(User.email == free_user["email"]).one()

        assert paper_svc.check_exits(db_session, user.id, {"BTC": 48000.0}) == 0
        t = client.get("/api/paper/list", headers=auth_headers).json()[0]
        assert t["trailing_active"] is True
        assert t["lowest_price"] == pytest.approx(48000.0)
        assert t["trailing_stop"] == pytest.approx(49440.0)

    def test_trailing_stop_never_moves_backward(self, client, auth_headers, db_session, free_user):
        client.post("/api/paper/open", json=_open_payload(), headers=auth_headers)
        user = db_session.query(User).filter(User.email == free_user["email"]).one()

        paper_svc.check_exits(db_session, user.id, {"BTC": 55000.0})
        before = client.get("/api/paper/list", headers=auth_headers).json()[0]["trailing_stop"]
        paper_svc.check_exits(db_session, user.id, {"BTC": 54000.0})
        after = client.get("/api/paper/list", headers=auth_headers).json()[0]["trailing_stop"]
        assert after == pytest.approx(before)


class TestListAndStats:
    def test_list_empty(self, client, auth_headers):
        assert client.get("/api/paper/list", headers=auth_headers).json() == []

    def test_stats_empty(self, client, auth_headers):
        data = client.get("/api/paper/stats", headers=auth_headers).json()
        assert data["total_trades"] == 0
        assert data["total_pnl_usd"] == 0.0

    def test_capital_reduces_after_open_position(self, client, auth_headers):
        first = client.post("/api/paper/open", json=_open_payload(), headers=auth_headers)
        assert first.status_code == 201
        second = client.post(
            "/api/paper/open",
            json=_open_payload(symbol="ETH", price=3000.0),
            headers=auth_headers,
        )
        assert second.status_code == 201
        assert second.json()["notional"] == pytest.approx(90.0)
