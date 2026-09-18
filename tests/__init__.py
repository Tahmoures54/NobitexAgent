# tests/__init__.py
"""
CryptoScanner test suite.

Test layout:
    conftest.py     — shared fixtures (in-memory DB, TestClient, users)
    test_auth.py    — register / login / me / logout
    test_scan.py    — cached scan, refresh, quota
    test_paper.py   — open / close / list / stats / SL-TP auto-close
    test_indicators.py  — existing pure-function tests (unchanged)
    test_signals.py     — existing strategy tests (unchanged)
    test_integration.py — existing end-to-end tests (unchanged)

Run all:
    pytest

Run a single file:
    pytest tests/test_auth.py -v

Run with coverage:
    pytest --cov=server --cov-report=term-missing
"""