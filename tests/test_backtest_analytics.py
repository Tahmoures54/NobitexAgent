from analytics.backtest import BacktestConfig, run_backtest


def _row(ts, price, global_price, spread=0.2, chase=0.2):
    return {
        "timestamp": ts,
        "symbol": "BTCIRT",
        "Ask": price,
        "Bid": price,
        "Global1hPct": 2.0,
        "ObservedGlobalPct": 1.0,
        "GlobalPriceUSD": global_price,
        "SpreadPct": spread,
        "ChasePct": chase,
    }


def test_backtest_exposes_performance_analytics():
    rows = [
        _row("2026-01-01T00:00:00+00:00", 100, 100),
        _row("2026-01-01T00:05:00+00:00", 104, 104),
        _row("2026-01-01T00:10:00+00:00", 108, 108),
        _row("2026-01-01T00:15:00+00:00", 101, 101),
    ]
    report = run_backtest(rows, BacktestConfig(initial_capital=1_000_000))
    data = report.to_dict()
    assert "equity_curve" in data
    assert "drawdown_curve" in data
    assert "signal_stats" in data
    assert "total_slippage_cost" in data
    assert report.trades == 1


def test_signal_rejections_are_counted():
    rows = [
        _row("2026-01-01T00:00:00+00:00", 100, 100),
        _row("2026-01-01T00:05:00+00:00", 101, 101, spread=2.0),
        _row("2026-01-01T00:10:00+00:00", 102, 102, chase=2.0),
    ]
    report = run_backtest(rows)
    assert report.signal_stats["rejected_spread"] >= 1
    assert report.signal_stats["rejected_chase"] >= 1


def test_entry_delay_uses_same_symbol_snapshots():
    rows = [
        _row("2026-01-01T00:00:00+00:00", 100, 100),
        dict(_row("2026-01-01T00:00:00+00:00", 200, 200), symbol="ETHIRT"),
        _row("2026-01-01T00:05:00+00:00", 101, 101),
        _row("2026-01-01T00:10:00+00:00", 102, 102),
    ]
    report = run_backtest(rows, BacktestConfig(entry_delay_scans=1))
    assert report.trades == 1
    assert report.trades_detail[0].entry_price > 100
