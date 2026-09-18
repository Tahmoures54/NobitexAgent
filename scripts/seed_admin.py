# scripts/seed_admin.py
"""
Create or update the admin user.

The admin email is taken from `settings.admin_email` (or overridden
with `--email`). The script:

    1. Looks up the user by email.
    2. Creates them if missing (with a random password unless provided).
    3. Upgrades the plan to `lifetime` if it isn't already.
    4. Prints the credentials exactly once.

Idempotent — re-running simply upgrades the plan if needed.
The password is only shown on creation.

Usage:
    python -m scripts.seed_admin
    python -m scripts.seed_admin --email admin@example.com
    python -m scripts.seed_admin --email admin@example.com --password "S3cret!"
"""
from __future__ import annotations

import argparse
import logging
import secrets
import sys
from pathlib import Path

# ── Project root on sys.path ───────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _bootstrap_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S",
    )


def _generate_password(length: int = 20) -> str:
    """URL-safe password with no ambiguous characters."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scripts.seed_admin",
        description="Create or upgrade the admin user.",
    )
    parser.add_argument(
        "--email", type=str, default=None,
        help="Admin email. Defaults to settings.admin_email.",
    )
    parser.add_argument(
        "--password", type=str, default=None,
        help="Admin password. If omitted, a random one is generated.",
    )
    parser.add_argument(
        "--no-upgrade", action="store_true",
        help="Do not force plan=lifetime on an existing user.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
    )
    args = parser.parse_args(argv)

    _bootstrap_logging(args.verbose)
    log = logging.getLogger("seed_admin")

    # Import after logging setup so any config warnings are visible
    from server.config import settings
    from server.database import SessionLocal, init_db
    from server.auth import hash_password
    from server.models import User

    # Make sure tables exist
    init_db()

    email = (args.email or settings.admin_email or "").strip().lower()
    if not email or "@" not in email:
        log.error(
            "No valid admin email. Set ADMIN_EMAIL in .env or pass --email."
        )
        return 2

    db = SessionLocal()
    created = False
    password_plain: str | None = None
    try:
        user = db.query(User).filter(User.email == email).first()

        if user is None:
            # Create
            password_plain = args.password or _generate_password()
            user = User(
                email=email,
                password_hash=hash_password(password_plain),
                plan="lifetime",
                plan_expires_at=None,
                is_active=True,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            created = True
            log.info("Admin user created: id=%d email=%s", user.id, user.email)
        else:
            # Update
            changes: list[str] = []
            if args.password:
                user.password_hash = hash_password(args.password)
                password_plain = args.password
                changes.append("password reset")
            if not args.no_upgrade and user.plan != "lifetime":
                user.plan = "lifetime"
                user.plan_expires_at = None
                changes.append(f"plan {user.plan!r} → 'lifetime'")
            if not user.is_active:
                user.is_active = True
                changes.append("re-activated")
            if changes:
                db.commit()
                db.refresh(user)
                log.info("Admin user updated: id=%d email=%s (%s)",
                         user.id, user.email, ", ".join(changes))
            else:
                log.info("Admin user already up to date: id=%d email=%s",
                         user.id, user.email)

        # Print credentials
        print()
        print("=" * 60)
        print("  ADMIN USER READY")
        print("=" * 60)
        print(f"  Email : {user.email}")
        if password_plain is not None:
            print(f"  Password : {password_plain}")
            print("  ⚠️  Save this password now — it will not be shown again.")
        else:
            print("  Password : (unchanged — use --password to reset)")
        print(f"  Plan : {user.plan}")
        print(f"  User ID : {user.id}")
        print("=" * 60)
        print()

        return 0
    except Exception as exc:
        db.rollback()
        log.exception("Failed to seed admin: %s", exc)
        return 3
    finally:
        db.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(130)