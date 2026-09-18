# scripts/__init__.py
"""
Command-line utilities for CryptoScanner.

All scripts in this package:
    - Ensure the project root is importable (`sys.path` insertion).
    - Read config from `server.config.settings` (so `.env` is honored).
    - Print progress to stdout using plain text.
    - Exit with a non-zero status code on failure.

Usage:
    python -m scripts.init_db
    python -m scripts.seed_admin --email admin@example.com
    python -m scripts.health_check
"""
from __future__ import annotations

__all__: list[str] = []