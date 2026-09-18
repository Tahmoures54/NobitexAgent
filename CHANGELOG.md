\# Changelog



All notable changes to this project are documented here.



The format is based on \[Keep a Changelog](https://keepachangelog.com/en/1.1.0/),

and this project adheres to \[Semantic Versioning](https://semver.org/spec/v2.0.0.html).



\---



\## \[Unreleased]



\### Planned

\- WebSocket live price feed

\- Telegram alert bot integration

\- AI Win Rate predictions

\- Interactive candlestick charts



\---



\## \[0.1.0] — 2025-01-01



Initial public MVP. Migrated from the desktop edition to a

browser-based SaaS architecture.



\### Added



\*\*Backend (`server/`)\*\*

\- FastAPI application with lifespan management

\- Pydantic-settings based configuration (`.env`)

\- SQLAlchemy 2.0 ORM with five tables:

&#x20; `users`, `trades`, `scan\_snapshots`, `scan\_usage`, `audit\_logs`

\- JWT authentication (HS256) with bcrypt password hashing

\- Rate limiting (`slowapi`) — per-IP on public endpoints

\- Background scheduler (APScheduler):

&#x20; - `scan\_job` — periodic market scan

&#x20; - `prices\_job` — paper SL/TP watcher

\- Structured logging to `logs/application.log`, `logs/security.log`,

&#x20; `logs/trading.log` with rotation

\- Audit log: every register/login/logout/plan-change/paper trade

\- Global exception handlers (IntegrityError → 409, rate limit → 429)

\- Alembic migrations

\- Standalone CLI tools (`scripts/`):

&#x20; - `init\_db.py` — initialize DB (Alembic first, `create\_all` fallback)

&#x20; - `seed\_admin.py` — create or upgrade the admin user

&#x20; - `health\_check.py` — standalone diagnostics



\*\*Service layer (`server/services/`)\*\*

\- `scanner.py` — market data → strategy → in-process cache

\- `prices.py` — live prices for open paper trades (cache + CoinGecko)

\- `paper.py` — open/close/stats + auto SL/TP

\- `plans.py` — Free/Pro daily scan quota

\- `audit.py` — fire-and-forget audit logger



\*\*HTTP routes (`server/routes/`)\*\*

\- `/health`, `/health/db`, `/health/full`

\- `/auth/register`, `/auth/login`, `/auth/token`, `/auth/me`, `/auth/logout`

\- `/api/scan`, `/api/scan/refresh`, `/api/scan/status`

\- `/api/paper/open`, `/api/paper/{id}/close`, `/api/paper/list`,

&#x20; `/api/paper/stats`, `DELETE /api/paper/{id}`

\- `/admin/stats`, `/admin/users`, `/admin/audit`,

&#x20; `/admin/users/{id}/plan`, `/admin/scan/run`



\*\*Frontend (`web/`)\*\*

\- `index.html` — live scanner with signal filters and search

\- `login.html` — combined sign-in / register

\- `dashboard.html` — user KPIs and recent trades

\- `paper.html` — open/close paper trades, auto-refresh

\- `pricing.html` — Free / Pro / Lifetime comparison

\- `terms.html`, `privacy.html`, `disclaimer.html` — legal pages

\- Dark teal theme matching the desktop edition

\- No build step: Alpine.js + Tailwind CDN



\*\*Testing (`tests/`)\*\*

\- `conftest.py` — isolated in-memory DB per test

\- `test\_auth.py` — register / login / me / logout / audit

\- `test\_scan.py` — cached read, refresh, guest/free/pro quota, rate limit

\- `test\_paper.py` — open / close / list / stats / auto SL/TP / isolation

\- `test\_indicators.py`, `test\_signals.py`, `test\_integration.py`

&#x20; (inherited from the desktop edition)



\*\*Scripts (`scripts/`)\*\*

\- `init\_db.py`, `seed\_admin.py`, `health\_check.py`



\*\*Entry point\*\*

\- `run.py` — dev (auto-reload) and prod (single worker) modes



\### Changed

\- Storage moved from `%APPDATA%` to project-local `data/`

\- Authentication: device-bound encryption replaced with JWT

\- UI: Tkinter desktop → browser (HTML/CSS/JS)

\- Rate limiting added to all auth and refresh endpoints



\### Removed

\- Desktop GUI (`gui/`)

\- Local `signal\_tracker.py` SQLite usage

\- Activation-code generator (replaced by admin plan assignment)



\### Security

\- bcrypt cost increased to 12 rounds

\- JWT signing key must be at least 64 random chars in production

\- Startup refuses to run in `--prod` mode if `SECRET\_KEY` is default

\- Audit log captures IP + user-agent for every security event



\---



\## Desktop edition (legacy)



The original Tkinter-based scanner is preserved in `archive/desktop/`

(not shipped with the web build). Its features (indicators, risk engine,

global lead engine) are reused unchanged by the web backend.



\---



\## Legend



\- \*\*Added\*\* — new features

\- \*\*Changed\*\* — changes in existing functionality

\- \*\*Deprecated\*\* — soon-to-be-removed features

\- \*\*Removed\*\* — removed features

\- \*\*Fixed\*\* — bug fixes

\- \*\*Security\*\* — security-relevant changes

