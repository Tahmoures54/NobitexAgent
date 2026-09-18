# CryptoScanner 7 — Nobitex Momentum Edition

CryptoScanner is a FastAPI-based crypto scanner with technical analysis, risk controls, paper trading, and a Nobitex IRT execution layer.

## What changed in 7.x

- Centralized Rial/Toman conversion.
- User-facing trade size is **1,000,000 Toman** by default.
- Nobitex exchange quote is handled as Rial/RLS at the API boundary.
- Added deterministic position sizing with balance, position and total-exposure caps.
- Added `TradingBot.place_notional_order()` and `place_configured_entry()` so strategy code no longer has to calculate base quantity manually.
- Paper and live execution are now mutually exclusive at the TradingBot boundary.
- A Nobitex account cannot receive live orders while `execution_mode` is `paper`.
- Added release validation and removed runtime/secret artifacts from release packaging.

## Money-unit rule

For Nobitex:

```text
1 Toman = 10 Rial/RLS
1,000,000 Toman = 10,000,000 Rial/RLS
```

The application accepts the user's position size in Toman and converts it exactly once at the execution boundary.

## Execution modes

`paper` is the safe default. `live` must be selected explicitly.

```json
{
  "exchange": "nobitex",
  "execution_mode": "paper",
  "enable_auto_trading": false,
  "fixed_position_toman": 1000000
}
```

Use `configs_nobitex_irt.example.json` as a starting template. API credentials are stored outside the project package by `BotConfig`; never put credentials into JSON, README files or source control.

## Install

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m scripts.init_db
python -m scripts.health_check
python main.py
```

Open `http://127.0.0.1:8000`.

## Validation

```bat
python scripts/release_check.py
python -m py_compile main.py server\main.py trading\bot_config.py trading\trader.py money\currency.py portfolio\sizing.py execution\protection.py
```

## Architecture

```text
Market Data
   └── Nobitex IRT market data
          ↓
Pump & Trend Signal Engine
          ↓
Risk Engine
          ↓
Portfolio / Position Sizing
          ↓
Execution Layer
          ├── Paper simulator
          └── Nobitex live adapter
```

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

Vercel uses the FastAPI application directly through:

```text
server.main:app
```

The root `main.py` is the local application launcher; it is not the Vercel ASGI entrypoint.

### Security

Never put Nobitex API credentials in GitHub, `.env.example`, Docker images, or frontend JavaScript. Store them as server-side environment/secret values.

```text
Root local entry point: main.py
FastAPI ASGI application: server.main:app
```
