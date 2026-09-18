# CryptoScanner 7 — Nobitex Momentum Edition

CryptoScanner is a FastAPI-based crypto scanner with technical analysis,
risk controls, paper trading, and a Nobitex IRT execution layer.

## What changed in 7.x

- Centralized Rial/Toman conversion.
- User-facing trade size is **1,000,000 Toman** by default.
- Nobitex exchange quote is handled as Rial/RLS at the API boundary.
- Added deterministic position sizing with balance, position and total-exposure caps.
- Added `TradingBot.place_notional_order()` and `place_configured_entry()` so strategy code no longer has to calculate base quantity manually.
- Paper and live execution are now mutually exclusive at the TradingBot boundary.
- A Nobitex account cannot receive live orders while `execution_mode` is `paper`.
- Preserved the global-lead design: global market data can lead Nobitex local IRT movement, while Nobitex remains the execution venue.
- Added release validation and removed runtime/secret artifacts from release packaging.

## Recommended Nobitex starting configuration

| Setting | Value |
|---|---:|
| User-facing entry size | 1,000,000 Toman |
| Nobitex quote equivalent | 10,000,000 Rial/RLS |
| Local/global movement threshold | 3% observed path |
| Stop loss | 2% |
| Trailing activation | 0.6% |
| Trailing distance | 1.2% |
| Take profit ceiling | 6% |
| Maximum simultaneous positions | 3 |
| Maximum new entries/cycle | 1 |
| Entry cooldown | 15 min |

These are risk-control defaults, not a guarantee of profitability. Strategy performance
must be validated with historical data, fees, spread, slippage and live execution behavior.

## Money-unit rule

For Nobitex:

```text
1 Toman = 10 Rial/RLS
1,000,000 Toman = 10,000,000 Rial/RLS
```

The application accepts the user's position size in Toman and converts it exactly once
at the execution boundary. Do not divide or multiply the amount again in UI, strategy,
tracker, or exchange code.

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

Use `configs_nobitex_irt.example.json` as a starting template. API credentials are
stored outside the project package by `BotConfig`; never put credentials into JSON,
README files or source control.

## Install

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m scripts.init_db
python -m scripts.health_check
python run.py
```

Open `http://127.0.0.1:8000`.

## Validation

```bat
python scripts/release_check.py
python -m py_compile trading\bot_config.py trading\trader.py money\currency.py portfolio\sizing.py execution\protection.py
```

## Architecture

```text
Market Data
   ├── Global feeds (CMC/CG)
   └── Nobitex IRT book
          ↓
Global Lead / Signal Engine
          ↓
Risk Engine
          ↓
Portfolio / Position Sizing
          ↓
Execution Layer
          ├── Paper simulator
          └── Nobitex live adapter
```

The strategy layer never needs to know whether a Nobitex amount is expressed in
Toman or Rial. That responsibility belongs to `money/` and `portfolio/`.

## Security

Do not distribute `.env`, encrypted credentials, SQLite runtime databases, WAL files,
logs containing sensitive information, `__pycache__`, or `.pyc` files.
