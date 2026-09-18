# tests/test_paper.py
"""Paper trading: open, close, list, stats, SL/TP auto-close."""
from __future__ import annotations

import pytest


# ══════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════
def _open_payload(**overrides) -> dict:
    base = {
        "symbol": "BTC",
        "side": "long",
        "price": 50000.0,
        "size_usd": 1000.0,
        "stop_loss_pct": 2.0,
        "take_profit_pct": 6.0,
    }
    base.update(overrides)
    return base


# ══════════════════════════════════════════════════════════
# Auth guard
# ══════════════════════════════════════════════════════════
class TestPaperAuthGuard:

    def test_open_requires_auth(self, client):
        r = client.post("/api/paper/open", json=_open_payload())
        assert r.status_code == 401

    def test_list_requires_auth(self, client):
        r = client.get("/api/paper/list")
        assert r.status_code == 401

    def test_stats_requires_auth(self, client):
        r = client.get("/api/paper/stats")
        assert r.status_code == 401


# ══════════════════════════════════════════════════════════
# Open
# ══════════════════════════════════════════════════════════
class TestOpenTrade:

    def test_open_long_trade(self, client, auth_headers):
        r = client.post(
            "/api/paper/open", json=_open_payload(), headers=auth_headers
        )
        assert r.status_code == 201, r.text
        t = r.json()
        assert t["symbol"] == "BTC"
        assert t["side"] == "long"
        assert t["status"] == "open"
        assert t["entry_price"] == 50000.0
        # position_size = 1000 / 50000 = 0.02
        assert t["position_size"] == pytest.approx(0.02, rel=1e-6)
        assert t["notional"] == pytest.approx(1000.0)
        # SL = 50000 * (1 - 0.02) = 49000
        assert t["stop_loss"] == pytest.approx(49000.0)
        # TP = 50000 * (1 + 0.06) = 53000
        assert t["take_profit"] == pytest.approx(53000.0)

    def test_open_short_trade(self, client, auth_headers):
        r = client.post(
            "/api/paper/open",
            json=_open_payload(side="short"),
            headers=auth_headers,
        )
        assert r.status_code == 201, r.text
        t = r.json()
        assert t["side"] == "short"
        # SL for short is ABOVE entry
        assert t["stop_loss"] == pytest.approx(51000.0)
        # TP for short is BELOW entry
        assert t["take_profit"] == pytest.approx(47000.0)

    def test_open_rejects_invalid_price(self, client, auth_headers):
        r = client.post(
            "/api/paper/open",
            json=_open_payload(price=0),
            headers=auth_headers,
        )
        assert r.status_code == 422

    def test_open_rejects_tiny_size(self, client, auth_headers):
        r = client.post(
            "/api/paper/open",
            json=_open_payload(size_usd=0.5),
            headers=auth_headers,
        )
        # size_usd < 1.0 is rejected by the route
        assert r.status_code in (400, 422)

    def test_open_symbol_normalized_to_uppercase(self, client, auth_headers):
        r = client.post(
            "/api/paper/open",
            json=_open_payload(symbol="btc"),
            headers=auth_headers,
        )
        assert r.status_code == 201
        assert r.json()["symbol"] == "BTC"


# ══════════════════════════════════════════════════════════
# Close
# ══════════════════════════════════════════════════════════
class TestCloseTrade:

    def test_close_long_profit(self, client, auth_headers):
        open_r = client.post(
            "/api/paper/open", json=_open_payload(), headers=auth_headers
        )
        trade_id = open_r.json()["id"]

        close_r = client.post(
            f"/api/paper/{trade_id}/close",
            json={"price": 53000.0, "reason": "Manual"},
            headers=auth_headers,
        )
        assert close_r.status_code == 200, close_r.text
        t = close_r.json()
        assert t["status"] == "closed"
        assert t["exit_price"] == 53000.0
        # pnl_pct = (53000 - 50000)/50000 * 100 = 6%
        assert t["pnl_pct"] == pytest.approx(6.0, abs=1e-3)
        # pnl_usd = 3000 * 0.02 = 60
        assert t["pnl_usd"] == pytest.approx(60.0, abs=1e-3)

    def test_close_long_loss(self, client, auth_headers):
        open_r = client.post(
            "/api/paper/open", json=_open_payload(), headers=auth_headers
        )
        trade_id = open_r.json()["id"]

        close_r = client.post(
            f"/api/paper/{trade_id}/close",
            json={"price": 48000.0, "reason": "Stop Loss"},
            headers=auth_headers,
        )
        assert close_r.status_code == 200
        t = close_r.json()
        # pnl_pct = (48000 - 50000)/50000 * 100 = -4%
        assert t["pnl_pct"] == pytest.approx(-4.0, abs=1e-3)
        assert t["pnl_usd"] == pytest.approx(-40.0, abs=1e-3)

    def test_close_short_profit(self, client, auth_headers):
        open_r = client.post(
            "/api/paper/open",
            json=_open_payload(side="short"),
            headers=auth_headers,
        )
        trade_id = open_r.json()["id"]

        close_r = client.post(
            f"/api/paper/{trade_id}/close",
            json={"price": 47000.0, "reason": "Take Profit"},
            headers=auth_headers,
        )
        assert close_r.status_code == 200
        # pnl_pct = (50000 - 47000)/50000 * 100 = 6%
        assert close_r.json()["pnl_pct"] == pytest.approx(6.0, abs=1e-3)

    def test_close_already_closed(self, client, auth_headers):
        open_r = client.post(
            "/api/paper/open", json=_open_payload(), headers=auth_headers
        )
        trade_id = open_r.json()["id"]

        # Close once
        client.post(
            f"/api/paper/{trade_id}/close",
            json={"price": 51000.0, "reason": "Manual"},
            headers=auth_headers,
        )
        # Try again
        r = client.post(
            f"/api/paper/{trade_id}/close",
            json={"price": 52000.0, "reason": "Manual"},
            headers=auth_headers,
        )
        assert r.status_code == 404

    def test_close_unknown_trade(self, client, auth_headers):
        r = client.post(
            "/api/paper/99999/close",
            json={"price": 100.0, "reason": "Manual"},
            headers=auth_headers,
        )
        assert r.status_code == 404


# ══════════════════════════════════════════════════════════
# Isolation between users
# ══════════════════════════════════════════════════════════
class TestUserIsolation:

    def test_user_cannot_see_other_users_trades(self, client, free_user):
        # Alice opens a trade
        alice_headers = {"Authorization": f"Bearer {free_user['token']}"}
        client.post(
            "/api/paper/open", json=_open_payload(), headers=alice_headers
        )

        # Bob registers and lists his trades
        bob = client.post(
            "/auth/register",
            json={"email": "bob@example.com", "password": "bobsecret123"},
        ).json()
        bob_headers = {"Authorization": f"Bearer {bob['access_token']}"}

        bob_list = client.get("/api/paper/list", headers=bob_headers)
        assert bob_list.status_code == 200
        assert bob_list.json() == []

        bob_stats = client.get("/api/paper/stats", headers=bob_headers)
        assert bob_stats.json()["total_trades"] == 0

    def test_user_cannot_close_other_users_trade(self, client, free_user):
        alice_headers = {"Authorization": f"Bearer {free_user['token']}"}
        alice_trade = client.post(
            "/api/paper/open", json=_open_payload(), headers=alice_headers
        ).json()

        bob = client.post(
            "/auth/register",
            json={"email": "bob2@example.com", "password": "bobsecret123"},
        ).json()
        bob_headers = {"Authorization": f"Bearer {bob['access_token']}"}

        r = client.post(
            f"/api/paper/{alice_trade['id']}/close",
            json={"price": 50000.0, "reason": "Manual"},
            headers=bob_headers,
        )
        assert r.status_code == 404


# ══════════════════════════════════════════════════════════
# List & Stats
# ══════════════════════════════════════════════════════════
class TestListAndStats:

    def test_list_empty(self, client, auth_headers):
        r = client.get("/api/paper/list", headers=auth_headers)
        assert r.status_code == 200
        assert r.json() == []

    def test_stats_empty(self, client, auth_headers):
        r = client.get("/api/paper/stats", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["total_trades"] == 0
        assert data["win_rate"] == 0.0
        assert data["total_pnl_usd"] == 0.0

    def test_stats_after_win_and_loss(self, client, auth_headers):
        # Trade 1: win
        t1 = client.post(
            "/api/paper/open", json=_open_payload(), headers=auth_headers
        ).json()
        client.post(
            f"/api/paper/{t1['id']}/close",
            json={"price": 53000.0, "reason": "Take Profit"},
            headers=auth_headers,
        )

        # Trade 2: loss
        t2 = client.post(
            "/api/paper/open",
            json=_open_payload(symbol="ETH", price=3000.0),
            headers=auth_headers,
        ).json()
        client.post(
            f"/api/paper/{t2['id']}/close",
            json={"price": 2900.0, "reason": "Stop Loss"},
            headers=auth_headers,
        )

        stats = client.get("/api/paper/stats", headers=auth_headers).json()
        assert stats["total_trades"] == 2
        assert stats["closed_trades"] == 2
        assert stats["wins"] == 1
        assert stats["losses"] == 1
        assert stats["win_rate"] == 50.0
        # pnl_usd: +60 (from BTC) + -33.33 (from ETH)
        # ETH: entry 3000, size_usd 1000 → qty 0.3333
        #      exit 2900 → -100 * 0.3333 = -33.33
        assert stats["total_pnl_usd"] == pytest.approx(26.67, abs=0.1)


# ══════════════════════════════════════════════════════════
# Auto SL/TP
# ══════════════════════════════════════════════════════════
class TestAutoSLTP:

    def test_auto_close_take_profit(self, client, auth_headers, db_session, free_user):
        # Open long BTC
        t = client.post(
            "/api/paper/open", json=_open_payload(), headers=auth_headers
        ).json()

        # Simulate price above TP
        from server.services import paper as paper_svc
        from server.models import User
        user = db_session.query(User).filter(User.email == free_user["email"]).first()
        closed = paper_svc.check_exits(
            db_session, user.id, {"BTC": 54000.0}  # above 53000 TP
        )
        assert closed == 1

        # Verify via API
        trades = client.get("/api/paper/list", headers=auth_headers).json()
        assert trades[0]["status"] == "closed"
        assert trades[0]["exit_reason"] == "Take Profit"

    def test_auto_close_stop_loss(self, client, auth_headers, db_session, free_user):
        t = client.post(
            "/api/paper/open", json=_open_payload(), headers=auth_headers
        ).json()

        from server.services import paper as paper_svc
        from server.models import User
        user = db_session.query(User).filter(User.email == free_user["email"]).first()
        closed = paper_svc.check_exits(
            db_session, user.id, {"BTC": 48000.0}  # below 49000 SL
        )
        assert closed == 1

        trades = client.get("/api/paper/list", headers=auth_headers).json()
        assert trades[0]["exit_reason"] == "Stop Loss"

    def test_auto_close_respects_short(self, client, auth_headers, db_session, free_user):
        t = client.post(
            "/api/paper/open",
            json=_open_payload(side="short"),
            headers=auth_headers,
        ).json()

        from server.services import paper as paper_svc
        from server.models import User
        user = db_session.query(User).filter(User.email == free_user["email"]).first()
        # For short: SL above entry (51000). Price 52000 should trigger.
        closed = paper_svc.check_exits(db_session, user.id, {"BTC": 52000.0})
        assert closed == 1
        assert (
            client.get("/api/paper/list", headers=auth_headers).json()[0]["exit_reason"]
            == "Stop Loss"
        )

    def test_no_auto_close_within_range(self, client, auth_headers, db_session, free_user):
        client.post("/api/paper/open", json=_open_payload(), headers=auth_headers)

        from server.services import paper as paper_svc
        from server.models import User
        user = db_session.query(User).filter(User.email == free_user["email"]).first()
        closed = paper_svc.check_exits(db_session, user.id, {"BTC": 50500.0})
        assert closed == 0

        trades = client.get("/api/paper/list", headers=auth_headers).json()
        assert trades[0]["status"] == "open"

    def test_auto_close_ignores_missing_price(self, client, auth_headers, db_session, free_user):
        client.post("/api/paper/open", json=_open_payload(), headers=auth_headers)

        from server.services import paper as paper_svc
        from server.models import User
        user = db_session.query(User).filter(User.email == free_user["email"]).first()
        # Price for a symbol not in the map → no close
        closed = paper_svc.check_exits(db_session, user.id, {"ETH": 3000.0})
        assert closed == 0


# ══════════════════════════════════════════════════════════
# Delete
# ══════════════════════════════════════════════════════════
class TestDeleteTrade:

    def test_delete_open_trade(self, client, auth_headers):
        t = client.post(
            "/api/paper/open", json=_open_payload(), headers=auth_headers
        ).json()
        r = client.delete(f"/api/paper/{t['id']}", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["deleted"] == t["id"]

        # Verify gone
        trades = client.get("/api/paper/list", headers=auth_headers).json()
        assert all(x["id"] != t["id"] for x in trades)

    def test_delete_closed_trade_fails(self, client, auth_headers):
        t = client.post(
            "/api/paper/open", json=_open_payload(), headers=auth_headers
        ).json()
        client.post(
            f"/api/paper/{t['id']}/close",
            json={"price": 51000.0, "reason": "Manual"},
            headers=auth_headers,
        )
        r = client.delete(f"/api/paper/{t['id']}", headers=auth_headers)
        assert r.status_code == 404