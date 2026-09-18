"""Cost-aware backtesting for the Global Lead -> Nobitex strategy."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional


def _f(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
        return x if x == x else default
    except (TypeError, ValueError):
        return default


def _symbol(row: Mapping[str, Any]) -> str:
    return str(row.get("symbol") or row.get("Symbol") or "").strip().upper()


def _price(row: Mapping[str, Any]) -> float:
    return _f(row.get("Ask") or row.get("local_ask") or row.get("ask") or row.get("Price") or row.get("price"))


def _bid(row: Mapping[str, Any], fallback: float) -> float:
    return _f(row.get("Bid") or row.get("local_bid") or row.get("bid"), fallback)


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
    duration_scans: int


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


def _signal(row: Mapping[str, Any], cfg: BacktestConfig) -> bool:
    global_move = _f(row.get("Global1hPct") or row.get("global_1h_pct") or row.get("global_move_pct"))
    observed = _f(row.get("ObservedGlobalPct") or row.get("ObservedGlobalMove (%)") or row.get("observed_global_pct") or row.get("observed_global_move_pct"), global_move)
    spread = _f(row.get("SpreadPct") or row.get("spread_pct"))
    chase = _f(row.get("ChasePct") or row.get("chase_pct"))
    return (
        global_move >= cfg.signal_min_global_move_pct
        and observed >= cfg.signal_min_observed_move_pct
        and spread <= cfg.max_spread_pct
        and chase <= cfg.max_chase_pct
    )


def _execution_price(price: float, side: str, slippage_pct: float) -> float:
    if side == "buy":
        return price * (1.0 + slippage_pct / 100.0)
    return price * (1.0 - slippage_pct / 100.0)


def run_backtest(rows: Iterable[Mapping[str, Any]], config: Optional[BacktestConfig] = None) -> BacktestReport:
    """Run a single-position backtest over timestamped multi-symbol snapshots.

    Only snapshots for the held symbol are used for its exit path. This avoids
    accidentally closing BTC with an ETH price when a feed is interleaved.
    """
    cfg = config or BacktestConfig()
    if cfg.initial_capital <= 0 or cfg.max_open_positions < 1:
        raise ValueError("initial_capital must be > 0 and max_open_positions must be >= 1")

    data = list(rows)
    data.sort(key=lambda r: str(r.get("timestamp") or r.get("Timestamp") or ""))
    capital = float(cfg.initial_capital)
    peak = capital
    max_dd_amount = max_dd_pct = 0.0
    total_fees = total_slippage_pct = 0.0
    trades: List[TradeResult] = []
    blocked_until = -1
    i = 0

    while i < len(data):
        row = data[i]
        symbol = _symbol(row)
        if not symbol or not _signal(row, cfg) or i <= blocked_until:
            i += 1
            continue

        entry_i = i
        remaining_delay = max(0, int(cfg.entry_delay_scans))
        while remaining_delay > 0:
            entry_i += 1
            while entry_i < len(data) and _symbol(data[entry_i]) != symbol:
                entry_i += 1
            if entry_i >= len(data):
                break
            remaining_delay -= 1
        if entry_i >= len(data):
            break
        entry_row = data[entry_i]
        entry_raw = _price(entry_row)
        if entry_raw <= 0:
            i += 1
            continue

        entry = _execution_price(entry_raw, "buy", cfg.slippage_pct_per_side)
        capital_used = capital * max(0.0, min(cfg.capital_usage_pct, 100.0)) / 100.0
        if capital_used <= 0:
            break
        qty = capital_used / entry
        entry_fee = capital_used * cfg.fee_pct_per_side / 100.0
        total_fees += entry_fee
        total_slippage_pct += cfg.slippage_pct_per_side

        highest = entry
        trailing_active = False
        exit_i: Optional[int] = None
        exit_raw = 0.0
        reason = "end_of_data"

        for j in range(entry_i + 1, len(data)):
            r = data[j]
            if _symbol(r) != symbol:
                continue
            raw = _bid(r, _price(r))
            if raw <= 0:
                continue
            highest = max(highest, raw)
            gain = (raw - entry) / entry * 100.0
            if gain >= cfg.trailing_activation_pct:
                trailing_active = True
            stop = entry * (1.0 - cfg.stop_loss_pct / 100.0)
            if trailing_active:
                stop = max(stop, highest * (1.0 - cfg.trailing_distance_pct / 100.0))
            if gain >= cfg.take_profit_pct:
                exit_i, exit_raw, reason = j, raw, "take_profit"
                break
            if raw <= stop:
                exit_i, exit_raw, reason = j, raw, ("trailing_stop" if trailing_active else "stop_loss")
                break

        if exit_i is None:
            for j in range(len(data) - 1, entry_i, -1):
                if _symbol(data[j]) == symbol:
                    exit_i, exit_raw, reason = j, _bid(data[j], _price(data[j])), "end_of_data"
                    break
        if exit_i is None or exit_raw <= 0:
            i += 1
            continue

        exit_price = _execution_price(exit_raw, "sell", cfg.slippage_pct_per_side)
        gross_pnl = (exit_price - entry) * qty
        exit_value = exit_price * qty
        exit_fee = exit_value * cfg.fee_pct_per_side / 100.0
        total_fees += exit_fee
        total_slippage_pct += cfg.slippage_pct_per_side
        net_pnl = gross_pnl - entry_fee - exit_fee
        capital += net_pnl

        entry_time = entry_row.get("timestamp") or entry_row.get("Timestamp")
        exit_time = data[exit_i].get("timestamp") or data[exit_i].get("Timestamp")
        duration = sum(1 for k in range(entry_i + 1, exit_i + 1) if _symbol(data[k]) == symbol)
        trades.append(TradeResult(
            symbol=symbol,
            entry_time=entry_time,
            exit_time=exit_time,
            entry_price=entry,
            exit_price=exit_price,
            gross_pct=(exit_raw - entry_raw) / entry_raw * 100.0,
            net_pct=net_pnl / capital_used * 100.0,
            pnl=net_pnl,
            fees=entry_fee + exit_fee,
            exit_reason=reason,
            capital_used=capital_used,
            duration_scans=duration,
        ))
        peak = max(peak, capital)
        dd_amount = max(0.0, peak - capital)
        dd_pct = dd_amount / peak * 100.0 if peak else 0.0
        max_dd_amount = max(max_dd_amount, dd_amount)
        max_dd_pct = max(max_dd_pct, dd_pct)
        blocked_until = exit_i
        i = exit_i + 1

    wins = [t for t in trades if t.net_pct > 0]
    losses = [t for t in trades if t.net_pct <= 0]
    gross_wins = sum(t.pnl for t in wins)
    gross_losses = abs(sum(t.pnl for t in losses))
    pf = gross_wins / gross_losses if gross_losses else (float("inf") if gross_wins else 0.0)
    streak = longest = 0
    for t in trades:
        streak = streak + 1 if t.net_pct <= 0 else 0
        longest = max(longest, streak)

    return BacktestReport(
        initial_capital=cfg.initial_capital,
        final_capital=capital,
        net_profit=capital - cfg.initial_capital,
        return_pct=(capital / cfg.initial_capital - 1.0) * 100.0,
        trades=len(trades),
        wins=len(wins),
        losses=len(losses),
        win_rate_pct=len(wins) / len(trades) * 100.0 if trades else 0.0,
        average_win_pct=sum(t.net_pct for t in wins) / len(wins) if wins else 0.0,
        average_loss_pct=sum(t.net_pct for t in losses) / len(losses) if losses else 0.0,
        profit_factor=pf,
        expectancy_pct=sum(t.net_pct for t in trades) / len(trades) if trades else 0.0,
        max_drawdown_pct=max_dd_pct,
        max_drawdown_amount=max_dd_amount,
        longest_losing_streak=longest,
        average_trade_duration_scans=sum(t.duration_scans for t in trades) / len(trades) if trades else 0.0,
        total_fees=total_fees,
        total_slippage_pct=total_slippage_pct,
        best_trade_pct=max((t.net_pct for t in trades), default=0.0),
        worst_trade_pct=min((t.net_pct for t in trades), default=0.0),
        trades_detail=trades,
    )
