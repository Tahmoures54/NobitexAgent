# server/routes/__init__.py
"""
HTTP route modules.

Each module owns one API surface and uses FastAPI's `APIRouter`.
Business logic lives in `server.services.*`; routes stay thin.
"""
from __future__ import annotations

__all__ = ["health", "auth", "scan", "paper", "admin"]