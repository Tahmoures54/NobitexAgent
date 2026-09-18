"""Run the strategy backtest against a CSV market snapshot export."""
from __future__ import annotations
import argparse
import csv
import json
from analytics.backtest import BacktestConfig, run_backtest


def main() -> int:
    p = argparse.ArgumentParser(description="Backtest Global Lead -> Nobitex strategy")
    p.add_argument("csv_file")
    p.add_argument("--capital", type=float, default=10_000_000)
    p.add_argument("--delay", type=int, default=0)
    p.add_argument("--fee", type=float, default=0.10)
    p.add_argument("--slippage", type=float, default=0.10)
    p.add_argument("--stop", type=float, default=3.0)
    p.add_argument("--trail", type=float, default=3.0)
    p.add_argument("--trail-activation", type=float, default=3.0)
    p.add_argument("--take-profit", type=float, default=50.0)
    p.add_argument("--usage", type=float, default=90.0)
    args = p.parse_args()
    with open(args.csv_file, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    report = run_backtest(rows, BacktestConfig(
        initial_capital=args.capital,
        entry_delay_scans=args.delay,
        fee_pct_per_side=args.fee,
        slippage_pct_per_side=args.slippage,
        stop_loss_pct=args.stop,
        trailing_distance_pct=args.trail,
        trailing_activation_pct=args.trail_activation,
        take_profit_pct=args.take_profit,
        capital_usage_pct=args.usage,
    ))
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
