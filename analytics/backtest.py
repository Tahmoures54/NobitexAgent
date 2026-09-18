"""Deterministic backtesting for the Global Lead -> Nobitex strategy.

The engine is intentionally independent from live execution. It consumes a
CSV-like sequence of market snapshots and models fees, spread, slippage,
entry delay, stop loss, trailing stop, and take profit. It is designed to
answer whether a configuration has positive expectancy before live capital
is increased.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Iterable, Mapping, Optional, Sequence, List, Dict, Any


def _f(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
        return x if x == x else default
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class BacktestConfig:
    initial_capital: float = 10_000_000.0
    fee_pct_per_side: float = 0.10
    slippage_pct_per_side: float = 0.10
    entry_delay_scans: int = 0
    stop_loss_pct: float = 3.0
    trailing_activation_pct: float = 3.0
    trailing_distance_pct: float = 3.0
    take_profit_pct: float = 50.0
    capital_usage_pct: float = 90.0
    max_open_positions: int = 1
    signal_min_global_move_pct: float = 1.2
    signal_min_observed_move_pct: float = 0.7
    max_chase_pct: float = 0.7
    max_spread_pct: float = 1.2


@dataclass
class TradeResult:
    symbol: str
    entry_time: Any
    exit_time: Any
    entry_price: float
    exit_price: float
    gross_pct: float
    net_pct: float
    pnl: float
    fees: float
    exit_reason: str
    capital_used: float


@dataclass
class BacktestReport:
    initial_capital: float
    final_capital: float
    net_profit: float
    return_pct: float
    trades: int
    wins: int
    losses: int
    win_rate_pct: float
    average_win_pct: float
    average_loss_pct: float
    profit_factor: float
    expectancy_pct: float
    max_drawdown_pct: float
    max_drawdown_amount: float
    longest_losing_streak: int
    average_trade_duration_scans: float
    total_fees: float
    total_slippage_pct: float
    best_trade_pct: float
    worst_trade_pct: float
    trades_detail: List[TradeResult]

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["trades_detail"] = [asdict(t) for t in self.trades_detail]
        return data


def _price(row: Mapping[str, Any]) -> float:
    # Ask is the conservative entry price; Bid is the conservative exit.
    return _f(row.get("Ask") or row.get("local_ask") or row.get("ask") or row.get("Price") or row.get("price"))


def _bid(row: Mapping[str, Any], fallback: float) -> float:
    return _f(row.get("Bid") or row.get("local_bid") or row.get("bid"), fallback)


def _signal(row: Mapping[str, Any], cfg: BacktestConfig) -> bool:
    global_move = _f(row.get("Global1hPct") or row.get("global_1h_pct") or row.get("global_move_pct"))
    observed = _f(row.get("ObservedGlobalPct") or row.get("observed_global_pct"), global_move)
    spread = _f(row.get("SpreadPct") or row.get("spread_pct"))
    chase = _f(row.get("ChasePct") or row.get("chase_pct"))
    return (
        global_move >= cfg.signal_min_global_move_pct
        and observed >= cfg.signal_min_observed_move_pct
        and spread <= cfg.max_spread_pct
        and chase <= cfg.max_chase_pct
    )


def _entry_exit_price(price: float, side: str, pct: float) -> float:
    factor = 1.0 + pct / 100.0 if side == "buy" else 1.0 - pct / 100.0
    return price * factor


def run_backtest(rows: Iterable[Mapping[str, Any]], config: Optional[BacktestConfig] = None) -> BacktestReport:
    cfg = config or BacktestConfig()
    if cfg.initial_capital <= 0:
        raise ValueError("initial_capital must be > 0")
    if cfg.max_open_positions < 1:
        raise ValueError("max_open_positions must be >= 1")

    data = list(rows)
    data.sort(key=lambda r: str(r.get("timestamp") or r.get("Timestamp") or ""))
    capital = float(cfg.initial_capital)
    peak = capital
    max_dd_amount = 0.0
    max_dd_pct = 0.0
    total_fees = 0.0
    total_slippage_pct = 0.0
    trades: List[TradeResult] = []
    cooldown_until = -1
    i = 0

    while i < len(data):
        row = data[i]
        symbol = str(row.get("symbol") or row.get("Symbol") or "").upper()
        if not symbol or not _signal(row, cfg) or i <= cooldown_until:
            i += 1
            continue

        entry_i = min(i + max(0, int(cfg.entry_delay_scans)), len(data) - 1)
        entry_row = data[entry_i]
        entry_raw = _price(entry_row)
        if entry_raw <= 0:
            i += 1
            continue
        entry = _entry_exit_price(entry_raw, "buy", cfg.slippage_pct_per_side)
        capital_used = capital * max(0.0, min(cfg.capital_usage_pct, 100.0)) / 100.0
        if capital_used <= 0:
            break
        qty = capital_used / entry
        entry_fee = capital_used * cfg.fee_pct_per_side / 100.0
        total_fees += entry_fee
        total_slippage_pct += cfg.slippage_pct_per_side

        highest = entry
        trailing_active = False
        exit_i = None
        exit_raw = None
        reason = "end_of_data"

        for j in range(entry_i + 1, len(data)):
            r = data[j]
            p = _bid(r, _price(r))
            if p <= 0:
                continue
            highest = max(highest, p)
            gain = (p - entry) / entry * 100.0
            if gain >= cfg.trailing_activation_pct:
                trailing_active = True
            stop = entry * (1.0 - cfg.stop_loss_pct / 100.0)
            if trailing_active:
                stop = max(stop, highest * (1.0 - cfg.trailing_distance_pct / 100.0))
            if gain >= cfg.take_profit_pct:
                exit_i, exit_raw, reason = j, p, "take_profit"
                break
            if p <= stop:
                exit_i, exit_raw, reason = j, p, "trailing_stop" if trailing_active else "stop_loss"
                break
            # Do not hold across an explicit symbol change in a mixed-symbol feed.
            next_symbol = str(r.get("symbol") or r.get("Symbol") or symbol).upper()
            if next_symbol != symbol:
                exit_i, exit_raw, reason = j, p, "symbol_change"
                break

        if exit_i is None:
            exit_i = len(data) - 1
            exit_raw = _bid(data[exit_i], _price(data[exit_i]))
        if not exit_raw or exit_raw <= 0:
            i += 1
            continue

        exit_price = _entry_exit_price(exit_raw, "sell", cfg.slippage_pct_per_side)
        gross_pnl = (exit_price - entry) * qty
        exit_value = exit_price * qty
        exit_fee = exit_value * cfg.fee_pct_per_side / 100.0
        total_fees += exit_fee
        total_slippage_pct += cfg.slippage_pct_per_side
        net_pnl = gross_pnl - entry_fee - exit_fee
        capital += net_pnl
        gross_pct = (exit_raw - entry_raw) / entry_raw * 100.0
        net_pct = net_pnl / capital_used * 100.0
        trades.append(TradeResult(
            symbol=symbol,
            entry_time=entry_row.get("timestamp") or entry_row.get("Timestamp"),
            exit_time=data[exit_i].get("timestamp") or data[exit_i].get("Timestamp"),
            entry_price=entry,
            exit_price=exit_price,
            gross_pct=gross_pct,
            net_pct=net_pct,
            pnl=net_pnl,
            fees=entry_fee + exit_fee,
            exit_reason=reason,
            capital_used=capital_used,
        ))
        peak = max(peak, capital)
        dd_amount = max(0.0, peak - capital)
        dd_pct = dd_amount / peak * 100.0 if peak else 0.0
        max_dd_amount = max(max_dd_amount, dd_amount)
        max_dd_pct = max(max_dd_pct, dd_pct)
        cooldown_until = exit_i
        i = exit_i + 1

    wins = [t for t in trades if t.net_pct > 0]
    losses = [t for t in trades if t.net_pct <= 0]
    gross_wins = sum(t.pnl for t in wins)
    gross_losses = abs(sum(t.pnl for t in losses))
    profit_factor = gross_wins / gross_losses if gross_losses > 0 else (float("inf") if gross_wins > 0 else 0.0)
    expectancy = sum(t.net_pct for t in trades) / len(trades) if trades else 0.0
    streak = longest = 0
    for t in trades:
        if t.net_pct <= 0:
            streak += 1
            longest = max(longest, streak)
        else:
            streak = 0
    durations = []
    # Duration is reconstructed from the sorted input timestamps only when numeric scan indexes are available.
    for t in trades:
        try:
            durations.append(max(0, data.index(next(r for r in data if (r.get("timestamp") or r.get("Timestamp")) == t.exit_time)) - data.index(next(r for r in data if (r.get("timestamp") or r.get("Timestamp")) == t.entry_time)))
        except Exception:
            pass
    return BacktestReport(
        initial_capital=cfg.initial_capital,
        final_capital=capital,
        net_profit=capital - cfg.initial_capital,
        return_pct=(capital / cfg.initial_capital - 1.0) * 100.0,
        trades=len(trades),
        wins=len(wins),
        losses=len(losses),
        win_rate_pct=(len(wins) / len(trades) * 100.0) if trades else 0.0,
        average_win_pct=sum(t.net_pct for t in wins) / len(wins) if wins else 0.0,
        average_loss_pct=sum(t.net_pct for t in losses) / len(losses) if losses else 0.0,
        profit_factor=profit_factor,
        expectancy_pct=expectancy,
        max_drawdown_pct=max_dd_pct,
        max_drawdown_amount=max_dd_amount,
        longest_losing_streak=longest,
        average_trade_duration_scans=sum(durations) / len(durations) if durations else 0.0,
        total_fees=total_fees,
        total_slippage_pct=total_slippage_pct,
        best_trade_pct=max((t.net_pct for t in trades), default=0.0),
        worst_trade_pct=min((t.net_pct for t in trades), default=0.0),
        trades_detail=trades,
    )
