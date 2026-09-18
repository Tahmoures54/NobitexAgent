from analytics.backtest import BacktestConfig, run_backtest


def test_profitable_trend_after_costs():
    rows = []
    for i, p in enumerate([100, 100, 101, 103, 106, 110, 108, 105]):
        rows.append({
            "timestamp": f"2026-01-01T00:{i:02d}:00",
            "symbol": "BTC",
            "Ask": p,
            "Bid": p * 0.999,
            "Global1hPct": 2.0,
            "ObservedGlobalPct": 2.0,
            "SpreadPct": 0.2,
            "ChasePct": 0.1,
        })
    report = run_backtest(rows, BacktestConfig(initial_capital=1_000_000))
    assert report.trades == 1
    assert report.final_capital > report.initial_capital
    assert report.profit_factor > 0


def test_costs_can_turn_small_move_negative():
    rows = []
    for i, p in enumerate([100, 100, 100.4, 100.3, 100.2]):
        rows.append({
            "timestamp": f"2026-01-01T00:{i:02d}:00",
            "symbol": "BTC",
            "Ask": p,
            "Bid": p * 0.999,
            "Global1hPct": 2.0,
            "ObservedGlobalPct": 2.0,
            "SpreadPct": 0.2,
            "ChasePct": 0.1,
        })
    report = run_backtest(rows, BacktestConfig(initial_capital=1_000_000))
    assert report.trades == 1
    assert report.net_profit < 0
