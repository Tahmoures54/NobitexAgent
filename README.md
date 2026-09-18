# CryptoScanner 7 — Nobitex Momentum Edition

CryptoScanner is a FastAPI-based crypto scanner with technical analysis, risk controls, paper trading, and a Nobitex IRT execution layer.

## Strategy architecture

The strategy is deliberately separated into two roles:

- **Global lead:** CoinMarketCap reference data detects sustained global momentum.
- **Local execution:** Nobitex IRT order-book data determines whether the local market has not already chased the move and whether execution quality is acceptable.

This is a lead/lag hypothesis, not a profitability guarantee. The project now includes a deterministic backtest engine so the hypothesis can be measured with fees, spread, slippage and execution delay before increasing live exposure.

## What changed in 7.x

- Centralized Rial/Toman conversion.
- User-facing trade size is **1,000,000 Toman** by default.
- Nobitex exchange quote is handled as Rial/RLS at the API boundary.
- Deterministic position sizing with balance, position and total-exposure caps.
- Paper and live execution are mutually exclusive at the TradingBot boundary.
- A Nobitex account cannot receive live orders while `execution_mode` is `paper`.
- Production startup refuses weak secrets, SQLite, and unsafe database bootstrap settings.
- Added a cost-aware **Global Lead → Nobitex backtest engine** with SL, trailing stop, TP, fees, slippage and entry-delay modelling.

## Profitability validation

Do not judge the strategy from win rate alone. The important outputs are net return, expectancy, profit factor, maximum drawdown, losing streak, fees and the complete trade list.

The backtest accepts a CSV snapshot export with these fields (aliases are also accepted):

    timestamp,symbol,Ask,Bid,Global1hPct,ObservedGlobalPct,SpreadPct,ChasePct

Run:

    python scripts\\run_backtest.py data\\market_snapshots.csv

Useful stress tests:

    python scripts\\run_backtest.py data\\market_snapshots.csv --delay 1 --fee 0.10 --slippage 0.20
    python scripts\\run_backtest.py data\\market_snapshots.csv --delay 2 --fee 0.10 --slippage 0.20

A result is not considered robust merely because one parameter set is profitable. Validate on separate development, validation and unseen test periods and include realistic costs.

## Money-unit rule

For Nobitex:

    1 Toman = 10 Rial/RLS
    1,000,000 Toman = 10,000,000 Rial/RLS

The application accepts the user's position size in Toman and converts it exactly once at the execution boundary.

## Execution modes

`paper` is the safe default. `live` must be selected explicitly.

    {
      "exchange": "nobitex",
      "execution_mode": "paper",
      "enable_auto_trading": false,
      "fixed_position_toman": 1000000
    }

Use `configs_nobitex_irt.example.json` as a starting template. API credentials are stored outside the project package by `BotConfig`; never put credentials into JSON, README files or source control.

## Install

    python -m venv .venv
    .venv\\Scripts\\activate
    pip install -r requirements.txt
    python -m scripts.init_db
    python -m scripts.health_check
    python main.py

Open `http://127.0.0.1:8000`.

## Validation

    python scripts/release_check.py
    python -m pytest -q
    python -m py_compile main.py server\\main.py trading\\bot_config.py trading\\trader.py money\\currency.py portfolio\\sizing.py execution\\protection.py analytics\\backtest.py

## Architecture

    Global reference feed (CMC)
              ↓
    Global Lead / Momentum Engine
              ↓
    Nobitex IRT order-book + local lag filters
              ↓
    Risk Engine
              ↓
    Portfolio / Position Sizing (Toman → Rial once)
              ↓
    Execution Layer
              ├── Paper simulator
              └── Nobitex live adapter

## Online deployment

The repository is deployable as a containerized FastAPI web application.

### Render

1. Create a Render account.
2. Create a **Blueprint** from this GitHub repository.
3. Render reads `render.yaml`, builds the Docker image, creates the Postgres database, and injects the required environment values.
4. Set `ADMIN_EMAIL` and required secrets in the Render dashboard.
5. Deploy and verify `/health`, `/health/db`, and `/docs`.

The web service and scheduler worker are separated in the current Render configuration.

### Vercel

Vercel uses the FastAPI application directly through `server.main:app`.

The root `main.py` is the local application launcher; it is not the Vercel ASGI entrypoint.

### Security

Never put Nobitex API credentials in GitHub, `.env.example`, Docker images, or frontend JavaScript. Store them as server-side environment/secret values.

    Root local entry point: main.py
    FastAPI ASGI application: server.main:app


## Production market-history worker

Market-history collection is intentionally separated from the Web/API process.

- **Web/API:** keep `RUN_SCHEDULER=false`. This is safe for Vercel or a Render web service.
- **Worker:** run `worker.py` with `RUN_SCHEDULER=true`. It runs the scanner every `SCAN_INTERVAL_MINUTES` (default 5 minutes) and persists each snapshot to PostgreSQL.
- The Render Blueprint runs `alembic upgrade head` before starting the worker, so the collector can start safely against the shared database.
- Set the same `CRYPTOSSCANNER_CMC_KEY` on the worker if you want persisted snapshots to contain CoinMarketCap global-lead fields used by the lead/lag backtest.
- The worker must use the same `DATABASE_URL` as the Web/API service.
- Do not run a second scheduler in the Web/API service; duplicate schedulers would create duplicate snapshots.

Render currently does not offer Free instances for Background Workers, so the always-on collector uses the smallest paid worker plan in the Blueprint.
