import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from portfolio import (
    PairPositionState,
    PortfolioConfig,
    PortfolioState,
    SharedPortfolioState,
    apply_buy,
    apply_sell,
    build_portfolio_config_from_env,
    compute_buy_fraction_multiplier,
    compute_buy_notional,
    compute_sell_units,
    compute_pair_ranking_results,
    get_pair_position_state,
    parse_pair_assets,
    shared_allocation_pct,
    shared_apply_buy,
    shared_apply_sell,
    shared_compute_buy_notional,
    shared_compute_sell_units,
    shared_position_value,
    shared_total_equity,
    total_equity,
)
from strategy import Strategy, build_strategy_from_env, evaluate_strategy


BINANCE_KLINE_COLUMN_COUNT = 12


@dataclass
class PriceBar:
    timestamp: str
    close: float


@dataclass
class CompletedTrade:
    entry_timestamp: str
    exit_timestamp: str
    quantity: float
    entry_price: float
    exit_price: float
    notional: float
    realized_pnl: float
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
    ending_position_value: float
    ending_equity: float
    total_return_pct: float
    completed_trades: List[CompletedTrade]
    first_timestamp: Optional[str]
    last_timestamp: Optional[str]
    max_drawdown_pct: float
    fees_paid: float
    realized_pnl: float


def write_trade_records_jsonl(trade_records: Sequence[Dict[str, object]], output_path: str) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file_handle:
        for record in trade_records:
            file_handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")


def write_timeseries_records_csv(timeseries_records: Sequence[Dict[str, object]], output_path: str) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames: List[str] = []
    seen_fields: set[str] = set()
    for record in timeseries_records:
        for key in record:
            if key not in seen_fields:
                seen_fields.add(key)
                fieldnames.append(key)

    with path.open("w", encoding="utf-8", newline="") as file_handle:
        writer = csv.DictWriter(file_handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in timeseries_records:
            writer.writerow(
                {
                    key: json.dumps(value, sort_keys=True)
                    if isinstance(value, (dict, list))
                    else value
                    for key, value in record.items()
                }
            )


def write_trade_records_csv(trade_records: Sequence[Dict[str, object]], output_path: str) -> None:
    write_timeseries_records_csv(trade_records, output_path)


def infer_pair_from_csv_path(csv_path: str) -> str:
    symbol = Path(csv_path).stem.split("-", 1)[0].upper()
    base_asset, quote_asset = parse_pair_assets(symbol)
    if quote_asset in {"USDT", "USDC", "BUSD"}:
        quote_asset = "USD"
    return f"{base_asset}/{quote_asset}"


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
    strategy: Optional[Strategy] = None,
    portfolio_config: Optional[PortfolioConfig] = None,
    include_trade_records: bool = False,
    include_timeseries_records: bool = False,
) -> Dict[str, object]:
    if starting_cash <= 0:
        raise ValueError("starting_cash must be positive.")
    if fee_rate < 0:
        raise ValueError("fee_rate cannot be negative.")

    strategy = strategy or build_strategy_from_env()
    portfolio_config = portfolio_config or build_portfolio_config_from_env()
    portfolio_state = PortfolioState(cash_balance=starting_cash)
    entry_timestamp: Optional[str] = None
    completed_trades: List[CompletedTrade] = []
    recent_closes: List[float] = []
    cooldown_remaining = 0
    bars_since_entry = 0
    buy_signals = 0
    sell_signals = 0
    executed_buys = 0
    executed_sells = 0
    skipped_buys = 0
    skipped_sells = 0
    peak_equity = starting_cash
    max_drawdown_pct = 0.0
    trade_records: List[Dict[str, object]] = []
    timeseries_records: List[Dict[str, object]] = []
    position_context: Dict[str, object] = {"in_trend_mode": False}

    for bar in bars:
        recent_closes.append(bar.close)
        decision = evaluate_strategy(
            recent_closes,
            in_position=portfolio_state.position_units > 0,
            entry_price=portfolio_state.average_entry_price,
            cooldown_remaining=cooldown_remaining,
            bars_since_entry=bars_since_entry,
            strategy=strategy,
            position_context=position_context,
        )
        position_context["in_trend_mode"] = bool(decision.debug.get("in_trend_mode", False))
        signal = decision.signal
        action_taken = "HOLD"

        if signal == "BUY":
            buy_signals += 1
            buy_fraction_pct_multiplier = compute_buy_fraction_multiplier(recent_closes, portfolio_config)
            buy_notional = compute_buy_notional(
                portfolio_state,
                bar.close,
                portfolio_config,
                buy_fraction_pct_override=decision.buy_fraction_pct_override,
                buy_fraction_pct_multiplier=buy_fraction_pct_multiplier,
            )
            if buy_notional <= 0:
                skipped_buys += 1
                action_taken = "SKIPPED_BUY"
            else:
                execution = apply_buy(portfolio_state, bar.close, fee_rate, buy_notional)
                if execution is None:
                    skipped_buys += 1
                    action_taken = "SKIPPED_BUY"
                else:
                    if entry_timestamp is None:
                        entry_timestamp = bar.timestamp
                        bars_since_entry = 0
                    cooldown_remaining = 0
                    executed_buys += 1
                    action_taken = "BUY"
                    if include_trade_records:
                        trade_records.append(
                            {
                                "timestamp": bar.timestamp,
                                "side": "BUY",
                                "signal_reason": decision.reason,
                                "price": bar.close,
                                "quantity": execution.quantity,
                                "notional": execution.notional,
                                "fee_paid": execution.fee_paid,
                                "buy_fraction_pct_multiplier": buy_fraction_pct_multiplier,
                                "portfolio_cash_after": portfolio_state.cash_balance,
                                "portfolio_units_after": portfolio_state.position_units,
                                "portfolio_equity_after": total_equity(portfolio_state, bar.close),
                                "average_entry_price_after": portfolio_state.average_entry_price,
                                "strategy": strategy.name,
                                "strategy_debug": decision.debug,
                            }
                        )
        elif signal == "SELL":
            sell_signals += 1
            if portfolio_state.position_units <= 0:
                skipped_sells += 1
                action_taken = "SKIPPED_SELL"
            else:
                units_to_sell = compute_sell_units(portfolio_state, bar.close, portfolio_config)
                if units_to_sell <= 0:
                    skipped_sells += 1
                    action_taken = "SKIPPED_SELL"
                else:
                    execution = apply_sell(portfolio_state, bar.close, fee_rate, units_to_sell)
                    if execution is None:
                        skipped_sells += 1
                        action_taken = "SKIPPED_SELL"
                    else:
                        completed_trades.append(
                            CompletedTrade(
                                entry_timestamp=entry_timestamp or bar.timestamp,
                                exit_timestamp=bar.timestamp,
                                quantity=execution.quantity,
                                entry_price=execution.average_entry_price or bar.close,
                                exit_price=bar.close,
                                notional=execution.notional,
                                realized_pnl=execution.realized_pnl,
                                return_pct=execution.return_pct,
                                exit_reason=decision.reason,
                            )
                        )
                        executed_sells += 1
                        action_taken = "SELL"
                        if include_trade_records:
                            trade_records.append(
                                {
                                    "timestamp": bar.timestamp,
                                    "side": "SELL",
                                    "signal_reason": decision.reason,
                                    "price": bar.close,
                                    "quantity": execution.quantity,
                                    "notional": execution.notional,
                                    "fee_paid": execution.fee_paid,
                                    "realized_pnl": execution.realized_pnl,
                                    "return_pct": execution.return_pct,
                                    "position_fully_closed": execution.position_fully_closed,
                                    "portfolio_cash_after": portfolio_state.cash_balance,
                                    "portfolio_units_after": portfolio_state.position_units,
                                    "portfolio_equity_after": total_equity(portfolio_state, bar.close),
                                    "average_entry_price_after": portfolio_state.average_entry_price,
                                    "strategy": strategy.name,
                                    "strategy_debug": decision.debug,
                                }
                            )
                        if execution.position_fully_closed:
                            entry_timestamp = None
                            bars_since_entry = 0
                            position_context["in_trend_mode"] = False
                            cooldown_remaining = strategy.config.cooldown_periods
        elif cooldown_remaining > 0:
            cooldown_remaining -= 1

        if portfolio_state.position_units > 0:
            bars_since_entry += 1
        else:
            bars_since_entry = 0

        current_equity = total_equity(portfolio_state, bar.close)
        if current_equity > peak_equity:
            peak_equity = current_equity
        if peak_equity > 0:
            drawdown_pct = ((peak_equity - current_equity) / peak_equity) * 100
            if drawdown_pct > max_drawdown_pct:
                max_drawdown_pct = drawdown_pct

        if include_timeseries_records:
            position_value = portfolio_state.position_units * bar.close
            allocation_pct = (position_value / current_equity) * 100 if current_equity > 0 else 0.0
            timeseries_records.append(
                {
                    "timestamp": bar.timestamp,
                    "price": bar.close,
                    "signal": signal,
                    "signal_reason": decision.reason,
                    "action_taken": action_taken,
                    "in_position": portfolio_state.position_units > 0,
                    "cash_balance": round(portfolio_state.cash_balance, 8),
                    "position_units": round(portfolio_state.position_units, 8),
                    "position_value": round(position_value, 8),
                    "equity": round(current_equity, 8),
                    "allocation_pct": round(allocation_pct, 8),
                    "average_entry_price": round(portfolio_state.average_entry_price, 8)
                    if portfolio_state.average_entry_price is not None
                    else None,
                    "fees_paid_to_date": round(portfolio_state.fees_paid, 8),
                    "realized_pnl_to_date": round(portfolio_state.realized_pnl, 8),
                    "cooldown_remaining": cooldown_remaining,
                    "bars_since_entry": bars_since_entry,
                    "in_trend_mode": bool(position_context.get("in_trend_mode", False)),
                    "peak_equity_to_date": round(peak_equity, 8),
                    "drawdown_pct": round(drawdown_pct, 8),
                    "strategy": strategy.name,
                    "strategy_debug": decision.debug,
                }
            )

    last_close = bars[-1].close
    ending_position_value = portfolio_state.position_units * last_close
    ending_equity = total_equity(portfolio_state, last_close)
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
        ending_cash=portfolio_state.cash_balance,
        ending_position_units=portfolio_state.position_units,
        ending_position_value=ending_position_value,
        ending_equity=ending_equity,
        total_return_pct=total_return_pct,
        completed_trades=completed_trades,
        first_timestamp=bars[0].timestamp if bars else None,
        last_timestamp=bars[-1].timestamp if bars else None,
        max_drawdown_pct=max_drawdown_pct,
        fees_paid=portfolio_state.fees_paid,
        realized_pnl=portfolio_state.realized_pnl,
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
        "ending_position_value": round(result.ending_position_value, 2),
        "ending_equity": round(result.ending_equity, 2),
        "max_drawdown_pct": round(result.max_drawdown_pct, 2),
        "total_return_pct": round(result.total_return_pct, 2),
        "fees_paid": round(result.fees_paid, 2),
        "realized_pnl": round(result.realized_pnl, 2),
        "completed_trades": len(result.completed_trades),
        "strategy": strategy.name,
        "strategy_config": strategy.to_dict()["config"],
        "portfolio_config": portfolio_config.to_dict(),
        "cooldown_periods": strategy.config.cooldown_periods,
        "winning_trades": winning_trades,
        "win_rate_pct": win_rate_pct,
        "exit_reasons": trades_by_reason,
        "trade_records": trade_records if include_trade_records else None,
        "timeseries_records": timeseries_records if include_timeseries_records else None,
    }


def run_multi_asset_backtest(
    csv_paths: Sequence[str],
    starting_cash: float = 10_000.0,
    fee_rate: float = 0.001,
    strategy: Optional[Strategy] = None,
    portfolio_config: Optional[PortfolioConfig] = None,
    include_trade_records: bool = False,
    include_timeseries_records: bool = False,
) -> Dict[str, object]:
    if len(csv_paths) < 2:
        raise ValueError("run_multi_asset_backtest requires at least two CSV paths.")
    if starting_cash <= 0:
        raise ValueError("starting_cash must be positive.")
    if fee_rate < 0:
        raise ValueError("fee_rate cannot be negative.")

    strategy = strategy or build_strategy_from_env()
    portfolio_config = portfolio_config or build_portfolio_config_from_env()

    pair_bars: Dict[str, List[PriceBar]] = {}
    for csv_path in csv_paths:
        pair = infer_pair_from_csv_path(csv_path)
        if pair in pair_bars:
            raise ValueError(f"Duplicate pair inferred for multi-asset backtest: {pair}")
        pair_bars[pair] = load_price_bars(csv_path)

    timestamps = sorted({bar.timestamp for bars in pair_bars.values() for bar in bars})
    indices = {pair: 0 for pair in pair_bars}
    price_histories: Dict[str, List[float]] = {pair: [] for pair in pair_bars}
    latest_prices: Dict[str, float] = {}
    pair_states: Dict[str, Dict[str, object]] = {
        pair: {
            "entry_timestamp": None,
            "cooldown_remaining": 0,
            "bars_since_entry": 0,
            "in_trend_mode": False,
            "buy_signals": 0,
            "sell_signals": 0,
            "executed_buys": 0,
            "executed_sells": 0,
            "skipped_buys": 0,
            "skipped_sells": 0,
        }
        for pair in pair_bars
    }
    portfolio_state = SharedPortfolioState(cash_balance=starting_cash)
    completed_trades: List[CompletedTrade] = []
    trade_records: List[Dict[str, object]] = []
    timeseries_records: List[Dict[str, object]] = []
    peak_equity = starting_cash
    max_drawdown_pct = 0.0

    for timestamp in timestamps:
        active_pairs: List[tuple[str, PriceBar]] = []
        for pair, bars in pair_bars.items():
            index = indices[pair]
            if index >= len(bars):
                continue
            bar = bars[index]
            if bar.timestamp != timestamp:
                continue
            indices[pair] += 1
            price_histories[pair].append(bar.close)
            latest_prices[pair] = bar.close
            active_pairs.append((pair, bar))

        pair_decisions: Dict[str, Dict[str, object]] = {}
        for pair, bar in sorted(active_pairs, key=lambda item: item[0]):
            state = pair_states[pair]
            position = get_pair_position_state(portfolio_state, pair)
            decision = evaluate_strategy(
                price_histories[pair],
                in_position=position.position_units > 0,
                entry_price=position.average_entry_price,
                cooldown_remaining=int(state["cooldown_remaining"]),
                bars_since_entry=int(state["bars_since_entry"]),
                strategy=strategy,
                position_context={"in_trend_mode": bool(state.get("in_trend_mode", False))},
            )
            state["in_trend_mode"] = bool(decision.debug.get("in_trend_mode", False))
            pair_decisions[pair] = {
                "bar": bar,
                "decision": decision,
                "position": position,
                "state": state,
            }
        pair_actions = {pair: "HOLD" for pair in pair_decisions}

        ranking_results = compute_pair_ranking_results(
            {
                pair: (price_histories[pair], pair_decisions[pair]["decision"].debug)
                for pair in pair_decisions
                if pair_decisions[pair]["decision"].signal == "BUY"
            },
            portfolio_config,
        )

        for pair in sorted(pair_decisions):
            decision = pair_decisions[pair]["decision"]
            if decision.signal != "SELL":
                continue

            bar = pair_decisions[pair]["bar"]
            position = pair_decisions[pair]["position"]
            state = pair_decisions[pair]["state"]
            state["sell_signals"] = int(state["sell_signals"]) + 1
            if position.position_units <= 0:
                state["skipped_sells"] = int(state["skipped_sells"]) + 1
                pair_actions[pair] = "SKIPPED_SELL"
                continue

            units_to_sell = shared_compute_sell_units(portfolio_state, pair, bar.close, portfolio_config)
            if units_to_sell <= 0:
                state["skipped_sells"] = int(state["skipped_sells"]) + 1
                pair_actions[pair] = "SKIPPED_SELL"
                continue

            execution = shared_apply_sell(portfolio_state, pair, bar.close, fee_rate, units_to_sell)
            if execution is None:
                state["skipped_sells"] = int(state["skipped_sells"]) + 1
                pair_actions[pair] = "SKIPPED_SELL"
                continue

            completed_trades.append(
                CompletedTrade(
                    entry_timestamp=str(state["entry_timestamp"] or bar.timestamp),
                    exit_timestamp=bar.timestamp,
                    quantity=execution.quantity,
                    entry_price=execution.average_entry_price or bar.close,
                    exit_price=bar.close,
                    notional=execution.notional,
                    realized_pnl=execution.realized_pnl,
                    return_pct=execution.return_pct,
                    exit_reason=decision.reason,
                )
            )
            state["executed_sells"] = int(state["executed_sells"]) + 1
            pair_actions[pair] = "SELL"

            if include_trade_records:
                trade_records.append(
                    {
                        "timestamp": bar.timestamp,
                        "pair": pair,
                        "side": "SELL",
                        "signal_reason": decision.reason,
                        "price": bar.close,
                        "quantity": execution.quantity,
                        "notional": execution.notional,
                        "fee_paid": execution.fee_paid,
                        "realized_pnl": execution.realized_pnl,
                        "return_pct": execution.return_pct,
                        "position_fully_closed": execution.position_fully_closed,
                        "portfolio_cash_after": portfolio_state.cash_balance,
                        "portfolio_units_after": position.position_units,
                        "portfolio_equity_after": shared_total_equity(portfolio_state, latest_prices),
                        "pair_position_value_after": shared_position_value(portfolio_state, pair, bar.close),
                        "pair_allocation_pct_after": shared_allocation_pct(portfolio_state, pair, latest_prices)
                        * 100,
                        "average_entry_price_after": position.average_entry_price,
                        "strategy": strategy.name,
                        "strategy_debug": decision.debug,
                    }
                )

            if execution.position_fully_closed:
                state["entry_timestamp"] = None
                state["bars_since_entry"] = 0
                state["in_trend_mode"] = False
                state["cooldown_remaining"] = strategy.config.cooldown_periods

        buy_pairs = sorted(
            (
                pair
                for pair in pair_decisions
                if pair_decisions[pair]["decision"].signal == "BUY"
            ),
            key=lambda pair: (
                -ranking_results.get(pair, {}).get("score", 0.0),
                pair,
            ),
        )
        if portfolio_config.max_ranked_buys_per_bar > 0:
            buy_pairs = buy_pairs[: portfolio_config.max_ranked_buys_per_bar]
        for pair in buy_pairs:
            bar = pair_decisions[pair]["bar"]
            decision = pair_decisions[pair]["decision"]
            position = pair_decisions[pair]["position"]
            state = pair_decisions[pair]["state"]
            state["buy_signals"] = int(state["buy_signals"]) + 1
            volatility_multiplier = compute_buy_fraction_multiplier(price_histories[pair], portfolio_config)
            pair_rank_multiplier = ranking_results.get(pair, {}).get("multiplier", 1.0)
            pair_ranking_score = ranking_results.get(pair, {}).get("score", 0.0)
            buy_fraction_pct_multiplier = volatility_multiplier * pair_rank_multiplier
            buy_notional = shared_compute_buy_notional(
                portfolio_state,
                pair,
                bar.close,
                latest_prices,
                portfolio_config,
                buy_fraction_pct_override=decision.buy_fraction_pct_override,
                buy_fraction_pct_multiplier=buy_fraction_pct_multiplier,
            )
            if buy_notional <= 0:
                state["skipped_buys"] = int(state["skipped_buys"]) + 1
                pair_actions[pair] = "SKIPPED_BUY"
                continue

            execution = shared_apply_buy(portfolio_state, pair, bar.close, fee_rate, buy_notional)
            if execution is None:
                state["skipped_buys"] = int(state["skipped_buys"]) + 1
                pair_actions[pair] = "SKIPPED_BUY"
                continue

            if state["entry_timestamp"] is None:
                state["entry_timestamp"] = bar.timestamp
                state["bars_since_entry"] = 0
            state["cooldown_remaining"] = 0
            state["executed_buys"] = int(state["executed_buys"]) + 1
            pair_actions[pair] = "BUY"

            if include_trade_records:
                trade_records.append(
                    {
                        "timestamp": bar.timestamp,
                        "pair": pair,
                        "side": "BUY",
                        "signal_reason": decision.reason,
                        "price": bar.close,
                        "quantity": execution.quantity,
                        "notional": execution.notional,
                        "fee_paid": execution.fee_paid,
                        "buy_fraction_pct_multiplier": buy_fraction_pct_multiplier,
                        "volatility_buy_fraction_multiplier": volatility_multiplier,
                        "pair_rank_multiplier": pair_rank_multiplier,
                        "pair_ranking_score": pair_ranking_score,
                        "portfolio_cash_after": portfolio_state.cash_balance,
                        "portfolio_units_after": position.position_units,
                        "portfolio_equity_after": shared_total_equity(portfolio_state, latest_prices),
                        "pair_position_value_after": shared_position_value(portfolio_state, pair, bar.close),
                        "pair_allocation_pct_after": shared_allocation_pct(portfolio_state, pair, latest_prices)
                        * 100,
                        "average_entry_price_after": position.average_entry_price,
                        "strategy": strategy.name,
                        "strategy_debug": decision.debug,
                    }
                )

        for pair in sorted(pair_decisions):
            state = pair_decisions[pair]["state"]
            decision = pair_decisions[pair]["decision"]
            if decision.signal not in {"BUY", "SELL"} and int(state["cooldown_remaining"]) > 0:
                state["cooldown_remaining"] = int(state["cooldown_remaining"]) - 1

        for pair, _ in active_pairs:
            state = pair_states[pair]
            position = get_pair_position_state(portfolio_state, pair)
            if position.position_units > 0:
                state["bars_since_entry"] = int(state["bars_since_entry"]) + 1
            else:
                state["bars_since_entry"] = 0

        current_equity = shared_total_equity(portfolio_state, latest_prices)
        if current_equity > peak_equity:
            peak_equity = current_equity
        if peak_equity > 0:
            drawdown_pct = ((peak_equity - current_equity) / peak_equity) * 100
            if drawdown_pct > max_drawdown_pct:
                max_drawdown_pct = drawdown_pct

        if include_timeseries_records:
            row: Dict[str, object] = {
                "timestamp": timestamp,
                "cash_balance": round(portfolio_state.cash_balance, 8),
                "equity": round(current_equity, 8),
                "fees_paid_to_date": round(portfolio_state.fees_paid, 8),
                "realized_pnl_to_date": round(portfolio_state.realized_pnl, 8),
                "peak_equity_to_date": round(peak_equity, 8),
                "drawdown_pct": round(drawdown_pct, 8),
                "active_pairs_count": len(active_pairs),
                "buy_signal_pairs": sum(
                    1 for pair in pair_decisions if pair_decisions[pair]["decision"].signal == "BUY"
                ),
                "sell_signal_pairs": sum(
                    1 for pair in pair_decisions if pair_decisions[pair]["decision"].signal == "SELL"
                ),
                "strategy": strategy.name,
            }
            for pair in sorted(pair_bars):
                pair_key = pair.replace("/", "_")
                position = get_pair_position_state(portfolio_state, pair)
                latest_price = latest_prices.get(pair)
                decision = pair_decisions.get(pair, {}).get("decision")
                row[f"{pair_key}_price"] = round(latest_price, 8) if latest_price is not None else None
                row[f"{pair_key}_signal"] = decision.signal if decision is not None else ""
                row[f"{pair_key}_signal_reason"] = decision.reason if decision is not None else ""
                row[f"{pair_key}_action_taken"] = pair_actions.get(pair, "")
                row[f"{pair_key}_position_units"] = round(position.position_units, 8)
                row[f"{pair_key}_position_value"] = round(
                    shared_position_value(portfolio_state, pair, latest_price or 0.0),
                    8,
                )
                row[f"{pair_key}_allocation_pct"] = round(
                    shared_allocation_pct(portfolio_state, pair, latest_prices) * 100,
                    8,
                )
                row[f"{pair_key}_average_entry_price"] = (
                    round(position.average_entry_price, 8)
                    if position.average_entry_price is not None
                    else None
                )
                row[f"{pair_key}_in_trend_mode"] = bool(pair_states[pair].get("in_trend_mode", False))
            timeseries_records.append(row)

    ending_positions: Dict[str, Dict[str, float]] = {}
    for pair in sorted(pair_bars):
        price = latest_prices.get(pair, 0.0)
        position = get_pair_position_state(portfolio_state, pair)
        ending_positions[pair] = {
            "position_units": round(position.position_units, 8),
            "position_value": round(position.position_units * price, 2),
            "average_entry_price": round(position.average_entry_price, 8)
            if position.average_entry_price is not None
            else None,
            "allocation_pct": round(shared_allocation_pct(portfolio_state, pair, latest_prices) * 100, 2),
        }

    total_return_pct = ((shared_total_equity(portfolio_state, latest_prices) - starting_cash) / starting_cash) * 100
    winning_trades = sum(1 for trade in completed_trades if trade.return_pct > 0)
    win_rate_pct = round((winning_trades / len(completed_trades)) * 100, 2) if completed_trades else 0.0
    exit_reasons: Dict[str, int] = {}
    for trade in completed_trades:
        exit_reasons[trade.exit_reason] = exit_reasons.get(trade.exit_reason, 0) + 1

    pair_summaries: Dict[str, Dict[str, object]] = {}
    for pair in sorted(pair_states):
        state = pair_states[pair]
        pair_summaries[pair] = {
            "rows": len(pair_bars[pair]),
            "buy_signals": int(state["buy_signals"]),
            "sell_signals": int(state["sell_signals"]),
            "executed_buys": int(state["executed_buys"]),
            "executed_sells": int(state["executed_sells"]),
            "skipped_buys": int(state["skipped_buys"]),
            "skipped_sells": int(state["skipped_sells"]),
            "ending_position_units": ending_positions[pair]["position_units"],
            "ending_position_value": ending_positions[pair]["position_value"],
            "ending_allocation_pct": ending_positions[pair]["allocation_pct"],
        }

    return {
        "rows": sum(len(bars) for bars in pair_bars.values()),
        "pairs": sorted(pair_bars),
        "pair_summaries": pair_summaries,
        "first_timestamp": min(bars[0].timestamp for bars in pair_bars.values()),
        "last_timestamp": max(bars[-1].timestamp for bars in pair_bars.values()),
        "buy_signals": sum(int(state["buy_signals"]) for state in pair_states.values()),
        "sell_signals": sum(int(state["sell_signals"]) for state in pair_states.values()),
        "executed_buys": sum(int(state["executed_buys"]) for state in pair_states.values()),
        "executed_sells": sum(int(state["executed_sells"]) for state in pair_states.values()),
        "skipped_buys": sum(int(state["skipped_buys"]) for state in pair_states.values()),
        "skipped_sells": sum(int(state["skipped_sells"]) for state in pair_states.values()),
        "starting_cash": round(starting_cash, 2),
        "ending_cash": round(portfolio_state.cash_balance, 2),
        "ending_equity": round(shared_total_equity(portfolio_state, latest_prices), 2),
        "ending_positions": ending_positions,
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "total_return_pct": round(total_return_pct, 2),
        "fees_paid": round(portfolio_state.fees_paid, 2),
        "realized_pnl": round(portfolio_state.realized_pnl, 2),
        "completed_trades": len(completed_trades),
        "strategy": strategy.name,
        "strategy_config": strategy.to_dict()["config"],
        "portfolio_config": portfolio_config.to_dict(),
        "winning_trades": winning_trades,
        "win_rate_pct": win_rate_pct,
        "exit_reasons": exit_reasons,
        "trade_records": trade_records if include_trade_records else None,
        "timeseries_records": timeseries_records if include_timeseries_records else None,
    }
