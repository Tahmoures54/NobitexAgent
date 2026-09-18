# server/__init__.py
"""
CryptoScanner Web Backend.

This package contains the FastAPI application, database models,
business services, and HTTP routes. The heavy analytics and trading
logic lives in `analysis/`, `api/`, and `trading/` at the project root
and is imported from here without modification.
"""
from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__"]