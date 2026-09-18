# server/services/__init__.py
"""
Business services layer.

Each service is a plain module (no classes) exposing pure functions
that take a `Session` or plain data and return plain data.

Rules:
    - Services never import from `server.routes` (no circular deps).
    - Services may import from `server.models`, `server.database`,
      `server.config`, `server.security`, `server.auth`.
    - Services log to `logging.getLogger(__name__)`.
"""
from __future__ import annotations

__all__ = [
    "scanner",
    "prices",
    "paper",
    "plans",
    "audit",
]