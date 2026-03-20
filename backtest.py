import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from strategy import StrategyConfig, evaluate_strategy


BINANCE_KLINE_COLUMN_COUNT = 12


@dataclass
class PriceBar:
    timestamp: str
    close: float


@dataclass
class CompletedTrade:
    entry_timestamp: str
    exit_timestamp: str
    entry_price: float
    exit_price: float
    return_pct: float
    exit_reason: str


@dataclass
class BacktestResult:
    rows: int
    buy_signals: int
    sell_signals: int
    executed_buys: int
    executed_sells: int
    skipped_buys: int
    skipped_sells: int
    starting_cash: float
    ending_cash: float
    ending_position_units: float
    ending_equity: float
    total_return_pct: float
    completed_trades: List[CompletedTrade]
    first_timestamp: Optional[str]
    last_timestamp: Optional[str]
    max_drawdown_pct: float


def _looks_like_number(value: str) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _normalize_timestamp(value: str) -> str:
    value = value.strip()
    if not value.isdigit():
        return value

    integer_value = int(value)
    if integer_value > 10**15:
        timestamp_seconds = integer_value / 1_000_000
    elif integer_value > 10**12:
        timestamp_seconds = integer_value / 1_000
    else:
        timestamp_seconds = integer_value

    return datetime.fromtimestamp(timestamp_seconds, tz=timezone.utc).isoformat()


def _parse_csv_row(row: Sequence[str]) -> Optional[PriceBar]:
    if not row:
        return None

    cleaned = [cell.strip() for cell in row]
    if not any(cleaned):
        return None

    first_cell = cleaned[0].lower()
    if first_cell in {"open_time", "timestamp", "date", "time"}:
        return None

    if len(cleaned) >= BINANCE_KLINE_COLUMN_COUNT and _looks_like_number(cleaned[4]):
        return PriceBar(timestamp=_normalize_timestamp(cleaned[0]), close=float(cleaned[4]))

    if len(cleaned) >= 2 and _looks_like_number(cleaned[1]):
        return PriceBar(timestamp=_normalize_timestamp(cleaned[0]), close=float(cleaned[1]))

    return None


def load_price_bars(csv_path: str) -> List[PriceBar]:
    bars: List[PriceBar] = []
    path = Path(csv_path)

    with path.open("r", encoding="utf-8", newline="") as file_handle:
        reader = csv.reader(file_handle)
        for row in reader:
            parsed_row = _parse_csv_row(row)
            if parsed_row is not None:
                bars.append(parsed_row)

    if not bars:
        raise ValueError(
            "No usable price rows found. Expected Binance kline CSV data or a two-column timestamp,close CSV."
        )

    return bars


def run_backtest(
    bars: Sequence[PriceBar],
    starting_cash: float = 10_000.0,
    fee_rate: float = 0.001,
    strategy_config: Optional[StrategyConfig] = None,
) -> Dict[str, object]:
    if starting_cash <= 0:
        raise ValueError("starting_cash must be positive.")
    if fee_rate < 0:
        raise ValueError("fee_rate cannot be negative.")

    strategy_config = strategy_config or StrategyConfig()
    cash = starting_cash
    position_units = 0.0
    entry_price: Optional[float] = None
    entry_timestamp: Optional[str] = None
    completed_trades: List[CompletedTrade] = []
    recent_closes: List[float] = []
    cooldown_remaining = 0
    buy_signals = 0
    sell_signals = 0
    executed_buys = 0
    executed_sells = 0
    skipped_buys = 0
    skipped_sells = 0
    peak_equity = starting_cash
    max_drawdown_pct = 0.0

    for bar in bars:
        recent_closes.append(bar.close)
        decision = evaluate_strategy(
            recent_closes,
            in_position=position_units > 0,
            entry_price=entry_price,
            cooldown_remaining=cooldown_remaining,
            config=strategy_config,
        )
        signal = decision.signal

        if signal == "BUY":
            buy_signals += 1
            if position_units > 0:
                skipped_buys += 1
                continue
            if cash <= 0:
                skipped_buys += 1
                continue

            units = (cash * (1 - fee_rate)) / bar.close
            position_units = units
            cash = 0.0
            entry_price = bar.close
            entry_timestamp = bar.timestamp
            cooldown_remaining = 0
            executed_buys += 1
        elif signal == "SELL":
            sell_signals += 1
            if position_units <= 0:
                skipped_sells += 1
                continue

            gross_value = position_units * bar.close
            cash = gross_value * (1 - fee_rate)
            realized_return_pct = 0.0
            if entry_price and entry_price > 0:
                realized_return_pct = ((bar.close - entry_price) / entry_price) * 100
            completed_trades.append(
                CompletedTrade(
                    entry_timestamp=entry_timestamp or bar.timestamp,
                    exit_timestamp=bar.timestamp,
                    entry_price=entry_price or bar.close,
                    exit_price=bar.close,
                    return_pct=realized_return_pct,
                    exit_reason=decision.reason,
                )
            )
            position_units = 0.0
            entry_price = None
            entry_timestamp = None
            cooldown_remaining = strategy_config.cooldown_periods
            executed_sells += 1
        elif cooldown_remaining > 0:
            cooldown_remaining -= 1

        current_equity = cash + (position_units * bar.close)
        if current_equity > peak_equity:
            peak_equity = current_equity
        if peak_equity > 0:
            drawdown_pct = ((peak_equity - current_equity) / peak_equity) * 100
            if drawdown_pct > max_drawdown_pct:
                max_drawdown_pct = drawdown_pct

    last_close = bars[-1].close
    ending_equity = cash + (position_units * last_close)
    total_return_pct = ((ending_equity - starting_cash) / starting_cash) * 100
    winning_trades = sum(1 for trade in completed_trades if trade.return_pct > 0)

    result = BacktestResult(
        rows=len(bars),
        buy_signals=buy_signals,
        sell_signals=sell_signals,
        executed_buys=executed_buys,
        executed_sells=executed_sells,
        skipped_buys=skipped_buys,
        skipped_sells=skipped_sells,
        starting_cash=starting_cash,
        ending_cash=cash,
        ending_position_units=position_units,
        ending_equity=ending_equity,
        total_return_pct=total_return_pct,
        completed_trades=completed_trades,
        first_timestamp=bars[0].timestamp if bars else None,
        last_timestamp=bars[-1].timestamp if bars else None,
        max_drawdown_pct=max_drawdown_pct,
    )

    if result.completed_trades:
        win_rate_pct = round((winning_trades / len(result.completed_trades)) * 100, 2)
    else:
        win_rate_pct = 0.0

    trades_by_reason: Dict[str, int] = {}
    for trade in result.completed_trades:
        trades_by_reason[trade.exit_reason] = trades_by_reason.get(trade.exit_reason, 0) + 1

    return {
        "rows": result.rows,
        "first_timestamp": result.first_timestamp,
        "last_timestamp": result.last_timestamp,
        "buy_signals": result.buy_signals,
        "sell_signals": result.sell_signals,
        "executed_buys": result.executed_buys,
        "executed_sells": result.executed_sells,
        "skipped_buys": result.skipped_buys,
        "skipped_sells": result.skipped_sells,
        "starting_cash": round(result.starting_cash, 2),
        "ending_cash": round(result.ending_cash, 2),
        "ending_position_units": round(result.ending_position_units, 8),
        "ending_equity": round(result.ending_equity, 2),
        "max_drawdown_pct": round(result.max_drawdown_pct, 2),
        "total_return_pct": round(result.total_return_pct, 2),
        "completed_trades": len(result.completed_trades),
        "cooldown_periods": strategy_config.cooldown_periods,
        "fast_ema_period": strategy_config.fast_ema_period,
        "slow_ema_period": strategy_config.slow_ema_period,
        "rsi_period": strategy_config.rsi_period,
        "rsi_buy_threshold": strategy_config.rsi_buy_threshold,
        "rsi_sell_threshold": strategy_config.rsi_sell_threshold,
        "stop_loss_pct": strategy_config.stop_loss_pct,
        "take_profit_pct": strategy_config.take_profit_pct,
        "winning_trades": winning_trades,
        "win_rate_pct": win_rate_pct,
        "exit_reasons": trades_by_reason,
    }
