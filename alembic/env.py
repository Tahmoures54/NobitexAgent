# alembic/env.py
"""
Alembic environment configuration.

Reads the database URL from `server.config.settings` so migrations
always target the same database the app uses.

Supports both offline (SQL script) and online (live DB) modes.
"""
from __future__ import annotations

import logging
import os
import sys
from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import engine_from_config, pool

# ── Ensure project root is importable ──────────────────────
# Alembic runs from the project root, but we add it explicitly so
# `import server` works regardless of how the CLI was invoked.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# ── Load app config & metadata ─────────────────────────────
from server.config import settings  # noqa: E402
from server.database import Base    # noqa: E402
from server import models           # noqa: F401,E402 — register tables on Base.metadata


# ── Alembic Config object ──────────────────────────────────
config = context.config

# Inject the real DB URL into the Alembic config
config.set_main_option("sqlalchemy.url", settings.database_url)


# ── Logging ────────────────────────────────────────────────
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

logger = logging.getLogger("alembic.env")


# ── Target metadata ────────────────────────────────────────
target_metadata = Base.metadata


# ══════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════
def _include_object(
    obj: Any,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: Any,
) -> bool:
    """
    Decide whether a database object should be included in autogenerate.

    - Ignores SQLite internal tables (`sqlite_*`).
    - Ignores views and internal sequences.
    - Everything else is included.
    """
    if type_ == "table" and name and name.startswith("sqlite_"):
        return False
    return True


def _process_revision_directives(
    context_ctx: Any,
    revision: Any,
    directives: list[Any],
) -> None:
    """
    Post-process autogenerate output.

    - Skip empty migrations (no changes) so we don't clutter `versions/`.
    """
    for directive in directives:
        if hasattr(directive, "upgrade_ops") and directive.upgrade_ops.is_empty():
            directives[:] = []
            logger.info("No schema changes detected; skipping empty migration.")
            return


# ══════════════════════════════════════════════════════════
# Offline mode (--sql)
# ══════════════════════════════════════════════════════════
def run_migrations_offline() -> None:
    """
    Emit SQL to stdout (or a file) without connecting to the DB.
    Useful for reviewing changes before applying them.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=_include_object,
        process_revision_directives=_process_revision_directives,
        render_as_batch=True,   # needed for SQLite ALTER support
    )

    with context.begin_transaction():
        context.run_migrations()


# ══════════════════════════════════════════════════════════
# Online mode (default)
# ══════════════════════════════════════════════════════════
def run_migrations_online() -> None:
    """Connect to the DB and run migrations."""
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = settings.database_url

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_object=_include_object,
            process_revision_directives=_process_revision_directives,
            render_as_batch=True,   # SQLite-friendly ALTER
        )

        with context.begin_transaction():
            context.run_migrations()


# ══════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()