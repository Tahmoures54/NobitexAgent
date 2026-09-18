# server/services/audit.py
"""
Audit log service.

Records security- and billing-relevant events into `audit_logs`.
Never raises — audit failures must not break the request.

Uses a dedicated logger (`server.security`) so the events also land
in `logs/security.log`.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from sqlalchemy.orm import Session

from server.logging_config import get_security_logger
from server.models import AuditLog

logger = logging.getLogger(__name__)
sec_logger = get_security_logger()


# ══════════════════════════════════════════════════════════
# Canonical event names
# ══════════════════════════════════════════════════════════
EV_LOGIN_OK = "login_ok"
EV_LOGIN_FAIL = "login_fail"
EV_REGISTER = "register"
EV_LOGOUT = "logout"
EV_TOKEN_REJECTED = "token_rejected"
EV_RATE_LIMITED = "rate_limited"
EV_PLAN_CHANGED = "plan_changed"
EV_ADMIN_ACCESS = "admin_access"
EV_PAPER_OPEN = "paper_open"
EV_PAPER_CLOSE = "paper_close"


# ══════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════
def log_event(
    db: Session,
    *,
    event: str,
    user_id: Optional[int] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
) -> None:
    """
    Persist one audit row and mirror it to the security logger.

    Never raises.
    """
    event = str(event)[:64]

    # 1. Structured log line
    try:
        sec_logger.info(
            "AUDIT %s | user=%s | ip=%s | ua=%s | details=%s",
            event,
            user_id if user_id is not None else "-",
            ip or "-",
            (user_agent or "-")[:120],
            _compact(details),
        )
    except Exception:
        pass

    # 2. DB row
    try:
        row = AuditLog(
            user_id=user_id,
            event=event,
            ip=(ip or "")[:64] or None,
            user_agent=(user_agent or "")[:255] or None,
            details=json.dumps(details, default=str) if details else None,
        )
        db.add(row)
        db.commit()
    except Exception as exc:
        # Roll back so the caller's session stays usable.
        try:
            db.rollback()
        except Exception:
            pass
        logger.debug("audit.log_event failed: %s", exc)


def log_event_safe(
    event: str,
    *,
    user_id: Optional[int] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
) -> None:
    """
    Log-only variant for cases where no DB session is available.
    Never raises.
    """
    try:
        sec_logger.info(
            "AUDIT %s | user=%s | ip=%s | ua=%s | details=%s",
            str(event)[:64],
            user_id if user_id is not None else "-",
            ip or "-",
            (user_agent or "-")[:120],
            _compact(details),
        )
    except Exception:
        pass


# ══════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════
def _compact(details: Optional[dict[str, Any]]) -> str:
    """One-line summary of a details dict."""
    if not details:
        return "-"
    try:
        return json.dumps(details, default=str, separators=(",", ":"))[:500]
    except Exception:
        return str(details)[:500]


def recent_events(
    db: Session,
    *,
    user_id: Optional[int] = None,
    event: Optional[str] = None,
    limit: int = 100,
) -> list[AuditLog]:
    """Query the last N audit events, optionally filtered."""
    q = db.query(AuditLog)
    if user_id is not None:
        q = q.filter(AuditLog.user_id == user_id)
    if event:
        q = q.filter(AuditLog.event == event)
    return q.order_by(AuditLog.created_at.desc()).limit(int(limit)).all()


__all__ = [
    "log_event",
    "log_event_safe",
    "recent_events",
    # Event constants
    "EV_LOGIN_OK",
    "EV_LOGIN_FAIL",
    "EV_REGISTER",
    "EV_LOGOUT",
    "EV_TOKEN_REJECTED",
    "EV_RATE_LIMITED",
    "EV_PLAN_CHANGED",
    "EV_ADMIN_ACCESS",
    "EV_PAPER_OPEN",
    "EV_PAPER_CLOSE",
]