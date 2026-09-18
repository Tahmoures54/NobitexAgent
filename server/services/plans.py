# server/services/plans.py
"""
Plan enforcement — daily scan quota for the Free tier.

Free users: FREE_DAILY_SCAN_LIMIT manual refreshes per UTC day.
Paid users (Pro / Lifetime): unlimited.

The `is_premium` helper lives in `server.security`. We import it here
so plan logic and authorization logic stay in sync.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from server.models import ScanUsage, User
from server.security import is_premium

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════
# Constants
# ══════════════════════════════════════════════════════════
FREE_DAILY_SCAN_LIMIT: int = 3


# ══════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════
def today_utc() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _get_usage(db: Session, user_id: int, day: datetime) -> int:
    row = (
        db.query(ScanUsage)
        .filter(ScanUsage.user_id == user_id, ScanUsage.day == day)
        .first()
    )
    return int(row.count) if row else 0


# ══════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════
def can_scan(db: Session, user: User, premium: bool | None = None) -> tuple[bool, int]:
    """
    Return (allowed, remaining_today).

    remaining_today == -1 means unlimited (paid plan).
    """
    if premium is None:
        premium = is_premium(user)

    if premium:
        return True, -1

    used = _get_usage(db, user.id, today_utc())
    remaining = max(0, FREE_DAILY_SCAN_LIMIT - used)
    return (remaining > 0), remaining


def record_scan(db: Session, user_id: int) -> int:
    """
    Increment today's usage by 1. Returns the new count.

    Safe against concurrent inserts thanks to the unique constraint.
    """
    day = today_utc()
    row = (
        db.query(ScanUsage)
        .filter(ScanUsage.user_id == user_id, ScanUsage.day == day)
        .first()
    )
    if row is None:
        row = ScanUsage(user_id=user_id, day=day, count=1)
        db.add(row)
        try:
            db.commit()
            db.refresh(row)
            return row.count
        except IntegrityError:
            # Another request inserted first; fall through to update path.
            db.rollback()
            row = (
                db.query(ScanUsage)
                .filter(ScanUsage.user_id == user_id, ScanUsage.day == day)
                .first()
            )
            if row is None:
                # Extremely unlikely; give up gracefully.
                logger.warning("record_scan: race + missing row for user=%d", user_id)
                return 0

    row.count = int(row.count) + 1
    db.commit()
    db.refresh(row)
    return row.count


def remaining_today(db: Session, user: User) -> int:
    """Convenience: remaining scans for today (-1 = unlimited)."""
    _, remaining = can_scan(db, user)
    return remaining


def reset_today(db: Session, user_id: int) -> None:
    """Delete today's usage row (admin / testing)."""
    day = today_utc()
    deleted = (
        db.query(ScanUsage)
        .filter(ScanUsage.user_id == user_id, ScanUsage.day == day)
        .delete()
    )
    db.commit()
    if deleted:
        logger.info("Reset today's scan usage for user=%d", user_id)


__all__ = [
    "FREE_DAILY_SCAN_LIMIT",
    "can_scan",
    "record_scan",
    "remaining_today",
    "reset_today",
    "today_utc",
]