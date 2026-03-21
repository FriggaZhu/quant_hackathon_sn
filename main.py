import argparse
import csv
import json
import logging
import os
import shutil
import time
from collections import deque
from dataclasses import asdict
from datetime import datetime, timezone
from math import floor
from pathlib import Path
from typing import Any, Dict, Optional

from api import RoostooAPIError, RoostooClient
from backtest import (
    infer_pair_from_csv_path,
    load_price_bars,
    run_backtest,
    run_multi_asset_backtest,
    write_trade_records_csv,
    write_trade_records_jsonl,
    write_timeseries_records_csv,
)
from portfolio import (
    PairPositionState,
    PortfolioConfig,
    PortfolioState,
    POSITION_EPSILON,
    SharedPortfolioState,
    allocation_pct,
    apply_buy,
    apply_sell,
    build_portfolio_config_from_env,
    compute_buy_fraction_multiplier,
    compute_buy_notional,
    compute_pair_ranking_results,
    compute_sell_units,
    get_pair_position_state,
    parse_pair_assets,
    position_value,
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


def get_configured_pairs() -> list[str]:
    raw_pairs = os.getenv("ROOSTOO_PAIRS", "").strip()
    if raw_pairs:
        pairs = [pair.strip() for pair in raw_pairs.split(",") if pair.strip()]
        if pairs:
            return pairs
    return [os.getenv("ROOSTOO_PAIR", "BTC/USD")]


CONFIGURED_PAIRS = get_configured_pairs()
PAIR = CONFIGURED_PAIRS[0]
POLL_INTERVAL_SECONDS = int(os.getenv("ROOSTOO_POLL_INTERVAL", "60"))
PRICE_HISTORY_SIZE = int(os.getenv("ROOSTOO_HISTORY_SIZE", "20"))
LIVE_TRADING_ENABLED = os.getenv("ROOSTOO_ENABLE_TRADING", "false").lower() == "true"
LOG_DIR = Path(os.getenv("ROOSTOO_LOG_DIR", "logs"))
BACKTEST_OUTPUT_DIR = Path(os.getenv("ROOSTOO_BACKTEST_OUTPUT_DIR", "artifacts/backtests"))
BACKTEST_SUMMARY_DIR = BACKTEST_OUTPUT_DIR / "summary"
BACKTEST_RUNS_DIR = BACKTEST_OUTPUT_DIR / "runs"
BOT_LOG_FILE = LOG_DIR / "bot.log"
TRADE_HISTORY_FILE = LOG_DIR / "trade_history.jsonl"
EXECUTION_HISTORY_FILE = LOG_DIR / "execution_history.jsonl"
STARTING_BALANCE_FILE = LOG_DIR / "starting_wallet.json"
PORTFOLIO_STATE_FILE = LOG_DIR / "portfolio_state.json"
BACKTEST_LATEST_SUMMARY_FILE = BACKTEST_SUMMARY_DIR / "backtest_latest_summary.json"
BACKTEST_HISTORY_JSONL_FILE = BACKTEST_SUMMARY_DIR / "backtest_runs.jsonl"
BACKTEST_HISTORY_CSV_FILE = BACKTEST_SUMMARY_DIR / "backtest_runs.csv"
BACKTEST_INITIAL_CASH = float(os.getenv("ROOSTOO_BACKTEST_INITIAL_CASH", "10000"))
BACKTEST_FEE_RATE = float(os.getenv("ROOSTOO_BACKTEST_FEE_RATE", "0.001"))
ACTIVE_STRATEGY: Strategy = build_strategy_from_env()
PORTFOLIO_CONFIG: PortfolioConfig = build_portfolio_config_from_env()

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

BACKTEST_HISTORY_CSV_FIELDS = [
    "run_timestamp_utc",
    "run_timestamp_label",
    "strategy",
    "pair",
    "pairs",
    "csv_path",
    "csv_paths",
    "first_timestamp",
    "last_timestamp",
    "starting_cash",
    "ending_cash",
    "ending_equity",
    "total_return_pct",
    "max_drawdown_pct",
    "fees_paid",
    "realized_pnl",
    "completed_trades",
    "winning_trades",
    "win_rate_pct",
    "buy_signals",
    "sell_signals",
    "executed_buys",
    "executed_sells",
    "skipped_buys",
    "skipped_sells",
    "trade_records_count",
    "trade_records_path",
    "trade_records_timestamped_path",
    "trade_records_csv_path",
    "trade_records_csv_timestamped_path",
    "timeseries_records_count",
    "timeseries_records_csv_path",
    "timeseries_records_csv_timestamped_path",
    "exit_reasons",
    "pair_summaries",
    "strategy_config",
    "portfolio_config",
]


def configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

    if logger.handlers:
        return

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(BOT_LOG_FILE)
    file_handler.setFormatter(formatter)

    logger.addHandler(stream_handler)
    logger.addHandler(file_handler)


def append_trade_history(entry: Dict[str, Any]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with TRADE_HISTORY_FILE.open("a", encoding="utf-8") as file_handle:
        file_handle.write(json.dumps(entry) + "\n")


def append_execution_history(entry: Dict[str, Any]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with EXECUTION_HISTORY_FILE.open("a", encoding="utf-8") as file_handle:
        file_handle.write(json.dumps(entry) + "\n")


def _serialize_backtest_field(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return value


def _run_timestamp_label(run_timestamp: datetime) -> str:
    return run_timestamp.astimezone(timezone.utc).strftime("%Y%m%d_%H%M%S_UTC")


def _timestamped_copy_path(output_path: str, timestamp_label: str) -> Path:
    path = Path(output_path)
    return path.with_name(f"{path.stem}_{timestamp_label}{path.suffix}")


def persist_backtest_summary(summary: Dict[str, Any]) -> Dict[str, str]:
    BACKTEST_SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    BACKTEST_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    run_timestamp = datetime.now(timezone.utc)
    timestamp_label = _run_timestamp_label(run_timestamp)
    record = {
        "run_timestamp_utc": run_timestamp.isoformat(),
        "run_timestamp_label": timestamp_label,
        **summary,
    }

    with BACKTEST_LATEST_SUMMARY_FILE.open("w", encoding="utf-8") as file_handle:
        json.dump(record, file_handle, indent=2, sort_keys=True, default=str)

    run_summary_file = BACKTEST_RUNS_DIR / f"backtest_summary_{timestamp_label}.json"
    with run_summary_file.open("w", encoding="utf-8") as file_handle:
        json.dump(record, file_handle, indent=2, sort_keys=True, default=str)

    with BACKTEST_HISTORY_JSONL_FILE.open("a", encoding="utf-8") as file_handle:
        file_handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")

    csv_row = {field: _serialize_backtest_field(record.get(field)) for field in BACKTEST_HISTORY_CSV_FIELDS}
    csv_exists = BACKTEST_HISTORY_CSV_FILE.exists()
    with BACKTEST_HISTORY_CSV_FILE.open("a", encoding="utf-8", newline="") as file_handle:
        writer = csv.DictWriter(file_handle, fieldnames=BACKTEST_HISTORY_CSV_FIELDS)
        if not csv_exists:
            writer.writeheader()
        writer.writerow(csv_row)

    return {
        "run_timestamp_label": timestamp_label,
        "backtest_latest_summary_path": str(BACKTEST_LATEST_SUMMARY_FILE),
        "backtest_run_summary_path": str(run_summary_file),
        "backtest_history_jsonl_path": str(BACKTEST_HISTORY_JSONL_FILE),
        "backtest_history_csv_path": str(BACKTEST_HISTORY_CSV_FILE),
    }


def save_starting_wallet(wallet: Dict[str, float]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with STARTING_BALANCE_FILE.open("w", encoding="utf-8") as file_handle:
        json.dump(wallet, file_handle, indent=2, sort_keys=True)


def load_starting_wallet() -> Optional[Dict[str, float]]:
    if not STARTING_BALANCE_FILE.exists():
        return None

    with STARTING_BALANCE_FILE.open("r", encoding="utf-8") as file_handle:
        raw_data = json.load(file_handle)

    if not isinstance(raw_data, dict):
        return None

    summary: Dict[str, float] = {}
    for asset, value in raw_data.items():
        try:
            summary[asset] = float(value)
        except (TypeError, ValueError):
            continue

    return summary


def save_portfolio_state(
    portfolio_state: PortfolioState,
    cooldown_remaining: int,
    in_trend_mode: bool = False,
) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "portfolio": portfolio_state.to_dict(),
        "cooldown_remaining": cooldown_remaining,
        "in_trend_mode": in_trend_mode,
    }
    with PORTFOLIO_STATE_FILE.open("w", encoding="utf-8") as file_handle:
        json.dump(payload, file_handle, indent=2, sort_keys=True)


def save_shared_portfolio_state(
    portfolio_state: SharedPortfolioState,
    pair_states: Dict[str, Dict[str, object]],
) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "portfolio": portfolio_state.to_dict(),
        "pair_states": pair_states,
    }
    with PORTFOLIO_STATE_FILE.open("w", encoding="utf-8") as file_handle:
        json.dump(payload, file_handle, indent=2, sort_keys=True)


def load_portfolio_state() -> tuple[Optional[PortfolioState], int, bool]:
    if not PORTFOLIO_STATE_FILE.exists():
        return None, 0, False

    with PORTFOLIO_STATE_FILE.open("r", encoding="utf-8") as file_handle:
        raw_data = json.load(file_handle)

    if not isinstance(raw_data, dict):
        return None, 0, False

    portfolio_data = raw_data.get("portfolio")
    cooldown_remaining = raw_data.get("cooldown_remaining", 0)
    in_trend_mode = bool(raw_data.get("in_trend_mode", False))
    if not isinstance(portfolio_data, dict):
        return None, 0, False

    try:
        portfolio_state = PortfolioState(
            cash_balance=float(portfolio_data.get("cash_balance", 0.0)),
            position_units=float(portfolio_data.get("position_units", 0.0)),
            average_entry_price=(
                float(portfolio_data["average_entry_price"])
                if portfolio_data.get("average_entry_price") is not None
                else None
            ),
            realized_pnl=float(portfolio_data.get("realized_pnl", 0.0)),
            fees_paid=float(portfolio_data.get("fees_paid", 0.0)),
        )
    except (TypeError, ValueError):
        return None, 0, False

    try:
        parsed_cooldown = int(cooldown_remaining)
    except (TypeError, ValueError):
        parsed_cooldown = 0

    return portfolio_state, max(parsed_cooldown, 0), in_trend_mode


def load_shared_portfolio_state() -> tuple[Optional[SharedPortfolioState], Dict[str, Dict[str, object]]]:
    if not PORTFOLIO_STATE_FILE.exists():
        return None, {}

    with PORTFOLIO_STATE_FILE.open("r", encoding="utf-8") as file_handle:
        raw_data = json.load(file_handle)

    if not isinstance(raw_data, dict):
        return None, {}

    portfolio_data = raw_data.get("portfolio")
    if not isinstance(portfolio_data, dict):
        return None, {}

    positions_data = portfolio_data.get("positions", {})
    if not isinstance(positions_data, dict):
        positions_data = {}

    positions: Dict[str, PairPositionState] = {}
    for pair, position_data in positions_data.items():
        if not isinstance(position_data, dict):
            continue
        try:
            positions[pair] = PairPositionState(
                position_units=float(position_data.get("position_units", 0.0)),
                average_entry_price=(
                    float(position_data["average_entry_price"])
                    if position_data.get("average_entry_price") is not None
                    else None
                ),
            )
        except (TypeError, ValueError):
            continue

    try:
        portfolio_state = SharedPortfolioState(
            cash_balance=float(portfolio_data.get("cash_balance", 0.0)),
            positions=positions,
            realized_pnl=float(portfolio_data.get("realized_pnl", 0.0)),
            fees_paid=float(portfolio_data.get("fees_paid", 0.0)),
        )
    except (TypeError, ValueError):
        return None, {}

    raw_pair_states = raw_data.get("pair_states", {})
    pair_states: Dict[str, Dict[str, object]] = {}
    if isinstance(raw_pair_states, dict):
        for pair, state in raw_pair_states.items():
            if not isinstance(state, dict):
                continue
            pair_states[pair] = {
                "cooldown_remaining": int(state.get("cooldown_remaining", 0)),
                "bars_since_entry": int(state.get("bars_since_entry", 0)),
                "in_trend_mode": bool(state.get("in_trend_mode", False)),
            }

    return portfolio_state, pair_states


def initialize_portfolio_state(
    wallet: Dict[str, float],
    price: float,
) -> PortfolioState:
    base_asset, quote_asset = parse_pair_assets(PAIR)
    cash_balance = wallet.get(quote_asset, 0.0)
    position_units = wallet.get(base_asset, 0.0)
    average_entry_price = price if position_units > POSITION_EPSILON else None
    return PortfolioState(
        cash_balance=cash_balance,
        position_units=position_units,
        average_entry_price=average_entry_price,
    )


def sync_portfolio_state_with_wallet(
    portfolio_state: PortfolioState,
    wallet: Dict[str, float],
    price: float,
) -> None:
    if not LIVE_TRADING_ENABLED:
        return

    base_asset, quote_asset = parse_pair_assets(PAIR)
    portfolio_state.cash_balance = wallet.get(quote_asset, portfolio_state.cash_balance)
    wallet_position_units = wallet.get(base_asset, portfolio_state.position_units)

    if wallet_position_units <= POSITION_EPSILON:
        portfolio_state.position_units = 0.0
        portfolio_state.average_entry_price = None
        return

    portfolio_state.position_units = wallet_position_units
    if portfolio_state.average_entry_price is None:
        portfolio_state.average_entry_price = price


def initialize_shared_portfolio_state(
    wallet: Dict[str, float],
    prices_by_pair: Dict[str, float],
) -> SharedPortfolioState:
    if not CONFIGURED_PAIRS:
        raise ValueError("No configured pairs for shared portfolio initialization.")

    _, quote_asset = parse_pair_assets(CONFIGURED_PAIRS[0])
    positions: Dict[str, PairPositionState] = {}
    for pair in CONFIGURED_PAIRS:
        base_asset, pair_quote_asset = parse_pair_assets(pair)
        if pair_quote_asset != quote_asset:
            raise ValueError("All configured pairs must share the same quote asset.")
        units = wallet.get(base_asset, 0.0)
        positions[pair] = PairPositionState(
            position_units=units,
            average_entry_price=prices_by_pair.get(pair) if units > POSITION_EPSILON else None,
        )

    return SharedPortfolioState(
        cash_balance=wallet.get(quote_asset, 0.0),
        positions=positions,
    )


def sync_shared_portfolio_with_wallet(
    portfolio_state: SharedPortfolioState,
    wallet: Dict[str, float],
    prices_by_pair: Dict[str, float],
) -> None:
    if not LIVE_TRADING_ENABLED or not CONFIGURED_PAIRS:
        return

    _, quote_asset = parse_pair_assets(CONFIGURED_PAIRS[0])
    portfolio_state.cash_balance = wallet.get(quote_asset, portfolio_state.cash_balance)

    for pair in CONFIGURED_PAIRS:
        base_asset, _ = parse_pair_assets(pair)
        position = get_pair_position_state(portfolio_state, pair)
        wallet_units = wallet.get(base_asset, position.position_units)
        if wallet_units <= POSITION_EPSILON:
            position.position_units = 0.0
            position.average_entry_price = None
            continue

        position.position_units = wallet_units
        if position.average_entry_price is None:
            position.average_entry_price = prices_by_pair.get(pair)


def extract_spot_wallet(balance_response: Dict[str, Any]) -> Dict[str, Any]:
    data_candidates = [
        balance_response,
        balance_response.get("data"),
        balance_response.get("Data"),
    ]

    for candidate in data_candidates:
        if not isinstance(candidate, dict):
            continue

        spot_wallet = candidate.get("SpotWallet")
        if isinstance(spot_wallet, dict):
            return spot_wallet

    raise ValueError(
        f"Could not find SpotWallet in balance response: {balance_response}"
    )


def summarize_wallet(wallet: Dict[str, Any]) -> Dict[str, float]:
    summary: Dict[str, float] = {}

    for asset, value in wallet.items():
        if isinstance(value, dict):
            total = 0.0
            found_numeric = False
            for nested_key in ("Available", "available", "Free", "free", "Locked", "locked"):
                nested_value = value.get(nested_key)
                if nested_value is None:
                    continue
                try:
                    total += float(nested_value)
                    found_numeric = True
                except (TypeError, ValueError):
                    continue

            if found_numeric:
                summary[asset] = total
                continue

        try:
            summary[asset] = float(value)
        except (TypeError, ValueError):
            continue

    return summary


def extract_price(ticker_response: Dict[str, Any], pair: str) -> float:
    candidates = [
        ticker_response.get("price"),
        ticker_response.get("lastPrice"),
        ticker_response.get("last_price"),
    ]

    data = ticker_response.get("data")
    if isinstance(data, dict):
        candidates.extend(
            [
                data.get("price"),
                data.get("lastPrice"),
                data.get("last_price"),
            ]
        )

    data = ticker_response.get("Data")
    if isinstance(data, dict):
        candidates.extend(
            [
                data.get("price"),
                data.get("lastPrice"),
                data.get("last_price"),
                data.get("LastPrice"),
            ]
        )

        pair_data = data.get(pair)
        if isinstance(pair_data, dict):
            candidates.extend(
                [
                    pair_data.get("price"),
                    pair_data.get("lastPrice"),
                    pair_data.get("last_price"),
                    pair_data.get("LastPrice"),
                ]
            )

    for candidate in candidates:
        if candidate is None:
            continue
        try:
            return float(candidate)
        except (TypeError, ValueError):
            continue

    raise ValueError(f"Could not find a usable price in ticker response: {ticker_response}")


def _format_quantity_for_pair(client: RoostooClient, pair: str, quantity: float, price: float) -> Optional[tuple[float, str]]:
    pair_rules = client.get_pair_rules(pair)
    amount_precision = int(pair_rules.get("AmountPrecision", 8))
    min_order_notional = float(pair_rules.get("MiniOrder", 0.0))
    multiplier = 10**amount_precision
    adjusted_quantity = floor(quantity * multiplier) / multiplier
    adjusted_quantity = round(adjusted_quantity, amount_precision)
    if adjusted_quantity <= 0:
        return None
    if min_order_notional > 0 and adjusted_quantity * price < min_order_notional:
        return None
    quantity_text = f"{adjusted_quantity:.{amount_precision}f}".rstrip("0").rstrip(".")
    if not quantity_text:
        quantity_text = "0"
    return adjusted_quantity, quantity_text


def maybe_place_order(
    client: RoostooClient,
    pair: str,
    signal: str,
    price: float,
    portfolio_state: PortfolioState,
    portfolio_config: PortfolioConfig,
    buy_fraction_pct_override: Optional[float] = None,
    buy_fraction_pct_multiplier: Optional[float] = None,
) -> Dict[str, Any]:
    if signal not in {"BUY", "SELL"}:
        logger.info("Decision is HOLD. No order placed.")
        return {"status": "skipped", "reason": "hold"}

    if signal == "BUY":
        order_notional = compute_buy_notional(
            portfolio_state,
            price,
            portfolio_config,
            buy_fraction_pct_override=buy_fraction_pct_override,
            buy_fraction_pct_multiplier=buy_fraction_pct_multiplier,
        )
        if order_notional <= 0:
            return {
                "status": "skipped",
                "reason": "buy_size_unavailable",
                "side": signal,
                "pair": pair,
            }
        quantity = order_notional / price
    else:
        quantity = compute_sell_units(portfolio_state, price, portfolio_config)
        if quantity <= 0:
            return {
                "status": "skipped",
                "reason": "sell_size_unavailable",
                "side": signal,
                "pair": pair,
            }
        order_notional = quantity * price

    portfolio_before = portfolio_state.to_dict()
    formatted_quantity = _format_quantity_for_pair(client, pair, quantity, price)
    if formatted_quantity is None:
        return {
            "status": "skipped",
            "reason": "quantity_below_exchange_minimum",
            "side": signal,
            "pair": pair,
        }
    quantity, quantity_text = formatted_quantity

    if not LIVE_TRADING_ENABLED:
        logger.info(
            "Trading disabled. Would place %s order for %.8f %s (notional %.2f %s).",
            signal,
            quantity,
            pair.split("/", 1)[0],
            order_notional,
            pair.split("/", 1)[1] if "/" in pair else "quote",
        )
        execution = (
            apply_buy(portfolio_state, price, BACKTEST_FEE_RATE, order_notional)
            if signal == "BUY"
            else apply_sell(portfolio_state, price, BACKTEST_FEE_RATE, quantity)
        )
        return {
            "status": "dry_run",
            "side": signal,
            "quantity": quantity,
            "notional": order_notional,
            "pair": pair,
            "buy_fraction_pct_multiplier": buy_fraction_pct_multiplier,
            "portfolio_before": portfolio_before,
            "portfolio_after": portfolio_state.to_dict(),
            "execution": asdict(execution) if execution is not None else None,
        }

    result = client.place_order(pair=pair, side=signal, quantity=quantity_text)
    if not result.get("Success", False):
        logger.warning("Order rejected by exchange: %s", result)
        return {
            "status": "rejected",
            "side": signal,
            "quantity": quantity,
            "notional": order_notional,
            "pair": pair,
            "buy_fraction_pct_multiplier": buy_fraction_pct_multiplier,
            "response": result,
            "portfolio_before": portfolio_before,
            "portfolio_after": portfolio_before,
            "execution": None,
        }
    logger.info("Order placed successfully: %s", result)
    execution = (
        apply_buy(portfolio_state, price, BACKTEST_FEE_RATE, order_notional)
        if signal == "BUY"
        else apply_sell(portfolio_state, price, BACKTEST_FEE_RATE, quantity)
    )
    return {
        "status": "placed",
        "side": signal,
        "quantity": quantity,
        "notional": order_notional,
        "pair": pair,
        "buy_fraction_pct_multiplier": buy_fraction_pct_multiplier,
        "response": result,
        "portfolio_before": portfolio_before,
        "portfolio_after": portfolio_state.to_dict(),
        "execution": asdict(execution) if execution is not None else None,
    }


def maybe_place_shared_order(
    client: RoostooClient,
    pair: str,
    signal: str,
    price: float,
    portfolio_state: SharedPortfolioState,
    latest_prices: Dict[str, float],
    portfolio_config: PortfolioConfig,
    buy_fraction_pct_override: Optional[float] = None,
    buy_fraction_pct_multiplier: Optional[float] = None,
) -> Dict[str, Any]:
    if signal not in {"BUY", "SELL"}:
        return {"status": "skipped", "reason": "hold", "pair": pair}

    position = get_pair_position_state(portfolio_state, pair)

    if signal == "BUY":
        order_notional = shared_compute_buy_notional(
            portfolio_state,
            pair,
            price,
            latest_prices,
            portfolio_config,
            buy_fraction_pct_override=buy_fraction_pct_override,
            buy_fraction_pct_multiplier=buy_fraction_pct_multiplier,
        )
        if order_notional <= 0:
            return {"status": "skipped", "reason": "buy_size_unavailable", "side": signal, "pair": pair}
        quantity = order_notional / price
    else:
        quantity = shared_compute_sell_units(portfolio_state, pair, price, portfolio_config)
        if quantity <= 0:
            return {"status": "skipped", "reason": "sell_size_unavailable", "side": signal, "pair": pair}
        order_notional = quantity * price

    portfolio_before = portfolio_state.to_dict()
    formatted_quantity = _format_quantity_for_pair(client, pair, quantity, price)
    if formatted_quantity is None:
        return {"status": "skipped", "reason": "quantity_below_exchange_minimum", "side": signal, "pair": pair}
    quantity, quantity_text = formatted_quantity

    if not LIVE_TRADING_ENABLED:
        logger.info(
            "Trading disabled. Would place %s order for %.8f %s (notional %.2f %s).",
            signal,
            quantity,
            pair.split("/", 1)[0],
            order_notional,
            pair.split("/", 1)[1] if "/" in pair else "quote",
        )
        execution = (
            shared_apply_buy(portfolio_state, pair, price, BACKTEST_FEE_RATE, order_notional)
            if signal == "BUY"
            else shared_apply_sell(portfolio_state, pair, price, BACKTEST_FEE_RATE, quantity)
        )
    else:
        result = client.place_order(pair=pair, side=signal, quantity=quantity_text)
        if not result.get("Success", False):
            logger.warning("Order rejected by exchange for %s: %s", pair, result)
            return {
                "status": "rejected",
                "side": signal,
                "quantity": quantity,
                "notional": order_notional,
                "pair": pair,
                "buy_fraction_pct_multiplier": buy_fraction_pct_multiplier,
                "response": result,
                "portfolio_before": portfolio_before,
                "portfolio_after": portfolio_before,
                "position_after": position.to_dict(),
                "execution": None,
            }
        logger.info("Order placed successfully for %s: %s", pair, result)
        execution = (
            shared_apply_buy(portfolio_state, pair, price, BACKTEST_FEE_RATE, order_notional)
            if signal == "BUY"
            else shared_apply_sell(portfolio_state, pair, price, BACKTEST_FEE_RATE, quantity)
        )
        return {
            "status": "placed",
            "side": signal,
            "quantity": quantity,
            "notional": order_notional,
            "pair": pair,
            "buy_fraction_pct_multiplier": buy_fraction_pct_multiplier,
            "response": result,
            "portfolio_before": portfolio_before,
            "portfolio_after": portfolio_state.to_dict(),
            "position_after": position.to_dict(),
            "execution": asdict(execution) if execution is not None else None,
        }

    return {
        "status": "dry_run",
        "side": signal,
        "quantity": quantity,
        "notional": order_notional,
        "pair": pair,
        "buy_fraction_pct_multiplier": buy_fraction_pct_multiplier,
        "portfolio_before": portfolio_before,
        "portfolio_after": portfolio_state.to_dict(),
        "position_after": position.to_dict(),
        "execution": asdict(execution) if execution is not None else None,
    }


def get_current_wallet(client: RoostooClient) -> Dict[str, float]:
    balance_response = client.get_balance()
    return summarize_wallet(extract_spot_wallet(balance_response))


def print_balance() -> None:
    client = RoostooClient()
    wallet = get_current_wallet(client)
    print(json.dumps(wallet, indent=2, sort_keys=True))


def print_pnl() -> None:
    client = RoostooClient()
    current_wallet = get_current_wallet(client)
    starting_wallet = load_starting_wallet()

    if starting_wallet is None:
        print("No starting wallet recorded yet. Run the bot once first.")
        return

    pnl = calculate_wallet_change(starting_wallet, current_wallet)
    print(
        json.dumps(
            {
                "starting_wallet": starting_wallet,
                "current_wallet": current_wallet,
                "wallet_change": pnl,
            },
            indent=2,
            sort_keys=True,
        )
    )


def print_backtest(
    csv_paths: list[str],
    trade_records_out: Optional[str] = None,
    trade_records_csv_out: Optional[str] = None,
    timeseries_records_csv_out: Optional[str] = None,
) -> None:
    include_trade_records = trade_records_out is not None or trade_records_csv_out is not None
    include_timeseries_records = timeseries_records_csv_out is not None
    if len(csv_paths) == 1:
        csv_path = csv_paths[0]
        bars = load_price_bars(csv_path)
        summary = run_backtest(
            bars,
            starting_cash=BACKTEST_INITIAL_CASH,
            fee_rate=BACKTEST_FEE_RATE,
            strategy=ACTIVE_STRATEGY,
            portfolio_config=PORTFOLIO_CONFIG,
            include_trade_records=include_trade_records,
            include_timeseries_records=include_timeseries_records,
        )
        trade_records = summary.pop("trade_records", None)
        timeseries_records = summary.pop("timeseries_records", None)
        if trade_records is not None:
            if trade_records_out is not None:
                write_trade_records_jsonl(trade_records, trade_records_out)
                summary["trade_records_path"] = trade_records_out
            if trade_records_csv_out is not None:
                write_trade_records_csv(trade_records, trade_records_csv_out)
                summary["trade_records_csv_path"] = trade_records_csv_out
            if trade_records_out is not None or trade_records_csv_out is not None:
                summary["trade_records_count"] = len(trade_records)
        if timeseries_records is not None and timeseries_records_csv_out is not None:
            write_timeseries_records_csv(timeseries_records, timeseries_records_csv_out)
            summary["timeseries_records_csv_path"] = timeseries_records_csv_out
            summary["timeseries_records_count"] = len(timeseries_records)
        summary["csv_path"] = csv_path
        summary["pair"] = infer_pair_from_csv_path(csv_path)
        persisted_paths = persist_backtest_summary(summary)
        timestamp_label = persisted_paths["run_timestamp_label"]
        if trade_records_out is not None:
            timestamped_trade_records_out = _timestamped_copy_path(trade_records_out, timestamp_label)
            timestamped_trade_records_out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(trade_records_out, timestamped_trade_records_out)
            summary["trade_records_timestamped_path"] = str(timestamped_trade_records_out)
        if trade_records_csv_out is not None:
            timestamped_trade_records_csv_out = _timestamped_copy_path(trade_records_csv_out, timestamp_label)
            timestamped_trade_records_csv_out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(trade_records_csv_out, timestamped_trade_records_csv_out)
            summary["trade_records_csv_timestamped_path"] = str(timestamped_trade_records_csv_out)
        if timeseries_records_csv_out is not None:
            timestamped_timeseries_records_csv_out = _timestamped_copy_path(timeseries_records_csv_out, timestamp_label)
            timestamped_timeseries_records_csv_out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(timeseries_records_csv_out, timestamped_timeseries_records_csv_out)
            summary["timeseries_records_csv_timestamped_path"] = str(timestamped_timeseries_records_csv_out)
        summary.update(persisted_paths)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return

    summary = run_multi_asset_backtest(
        csv_paths,
        starting_cash=BACKTEST_INITIAL_CASH,
        fee_rate=BACKTEST_FEE_RATE,
        strategy=ACTIVE_STRATEGY,
        portfolio_config=PORTFOLIO_CONFIG,
        include_trade_records=include_trade_records,
        include_timeseries_records=include_timeseries_records,
    )
    trade_records = summary.pop("trade_records", None)
    timeseries_records = summary.pop("timeseries_records", None)
    if trade_records is not None:
        if trade_records_out is not None:
            write_trade_records_jsonl(trade_records, trade_records_out)
            summary["trade_records_path"] = trade_records_out
        if trade_records_csv_out is not None:
            write_trade_records_csv(trade_records, trade_records_csv_out)
            summary["trade_records_csv_path"] = trade_records_csv_out
        if trade_records_out is not None or trade_records_csv_out is not None:
            summary["trade_records_count"] = len(trade_records)
    if timeseries_records is not None and timeseries_records_csv_out is not None:
        write_timeseries_records_csv(timeseries_records, timeseries_records_csv_out)
        summary["timeseries_records_csv_path"] = timeseries_records_csv_out
        summary["timeseries_records_count"] = len(timeseries_records)
    summary["csv_paths"] = csv_paths
    persisted_paths = persist_backtest_summary(summary)
    timestamp_label = persisted_paths["run_timestamp_label"]
    if trade_records_out is not None:
        timestamped_trade_records_out = _timestamped_copy_path(trade_records_out, timestamp_label)
        timestamped_trade_records_out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(trade_records_out, timestamped_trade_records_out)
        summary["trade_records_timestamped_path"] = str(timestamped_trade_records_out)
    if trade_records_csv_out is not None:
        timestamped_trade_records_csv_out = _timestamped_copy_path(trade_records_csv_out, timestamp_label)
        timestamped_trade_records_csv_out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(trade_records_csv_out, timestamped_trade_records_csv_out)
        summary["trade_records_csv_timestamped_path"] = str(timestamped_trade_records_csv_out)
    if timeseries_records_csv_out is not None:
        timestamped_timeseries_records_csv_out = _timestamped_copy_path(timeseries_records_csv_out, timestamp_label)
        timestamped_timeseries_records_csv_out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(timeseries_records_csv_out, timestamped_timeseries_records_csv_out)
        summary["timeseries_records_csv_timestamped_path"] = str(timestamped_timeseries_records_csv_out)
    summary.update(persisted_paths)
    print(json.dumps(summary, indent=2, sort_keys=True))


def run_single_asset_bot() -> None:
    configure_logging()
    client = RoostooClient()
    history_size = max(PRICE_HISTORY_SIZE, ACTIVE_STRATEGY.required_history + 5)
    price_history = deque(maxlen=history_size)
    starting_wallet: Optional[Dict[str, float]] = None
    portfolio_state, cooldown_remaining, in_trend_mode = load_portfolio_state()
    bars_since_entry = 0

    logger.info("Starting Roostoo trading bot for %s", PAIR)
    logger.info("Live trading enabled: %s", LIVE_TRADING_ENABLED)
    logger.info("Active strategy: %s", ACTIVE_STRATEGY.name)
    logger.info("Strategy config: %s", ACTIVE_STRATEGY.config)
    logger.info("Portfolio config: %s", PORTFOLIO_CONFIG)

    while True:
        try:
            balance_response = client.get_balance()
            current_wallet = summarize_wallet(extract_spot_wallet(balance_response))
            if starting_wallet is None:
                starting_wallet = load_starting_wallet()
            if starting_wallet is None:
                starting_wallet = current_wallet.copy()
                save_starting_wallet(starting_wallet)

            ticker = client.get_ticker(PAIR)
            price = extract_price(ticker, PAIR)
            if portfolio_state is None:
                portfolio_state = initialize_portfolio_state(current_wallet, price)
            sync_portfolio_state_with_wallet(portfolio_state, current_wallet, price)

            price_history.append(price)
            wallet_change = calculate_wallet_change(starting_wallet, current_wallet)
            current_equity = total_equity(portfolio_state, price)
            current_position_value = position_value(portfolio_state, price)
            current_allocation = allocation_pct(portfolio_state, price)

            decision = evaluate_strategy(
                list(price_history),
                in_position=portfolio_state.position_units > 0,
                entry_price=portfolio_state.average_entry_price,
                cooldown_remaining=cooldown_remaining,
                bars_since_entry=bars_since_entry,
                strategy=ACTIVE_STRATEGY,
                position_context={"in_trend_mode": in_trend_mode},
            )
            signal = decision.signal
            debug = decision.debug
            in_trend_mode = bool(debug.get("in_trend_mode", False))
            buy_fraction_pct_multiplier = compute_buy_fraction_multiplier(list(price_history), PORTFOLIO_CONFIG)
            logger.info(
                "Latest price: %.8f | Signal: %s | Reason: %s | Strategy: %s | Debug: %s | Position units: %.8f | Cash: %.2f | Equity: %.2f | Allocation: %.2f%% | Cooldown: %d",
                price,
                signal,
                decision.reason,
                ACTIVE_STRATEGY.name,
                json.dumps(debug, sort_keys=True, default=str),
                portfolio_state.position_units,
                portfolio_state.cash_balance,
                current_equity,
                current_allocation * 100,
                cooldown_remaining,
            )
            logger.info("Spot wallet: %s", current_wallet)
            logger.info("Wallet change since start: %s", wallet_change)

            order_result = maybe_place_order(
                client,
                PAIR,
                signal,
                price,
                portfolio_state,
                PORTFOLIO_CONFIG,
                buy_fraction_pct_override=decision.buy_fraction_pct_override,
                buy_fraction_pct_multiplier=buy_fraction_pct_multiplier if signal == "BUY" else None,
            )
            if signal == "BUY" and order_result.get("status") in {"dry_run", "placed"}:
                if portfolio_state.position_units > 0 and bars_since_entry == 0:
                    bars_since_entry = 0
                cooldown_remaining = 0
            elif signal == "SELL" and order_result.get("status") in {"dry_run", "placed"}:
                if portfolio_state.position_units <= POSITION_EPSILON:
                    bars_since_entry = 0
                    in_trend_mode = False
                    cooldown_remaining = ACTIVE_STRATEGY.config.cooldown_periods
            elif cooldown_remaining > 0:
                cooldown_remaining -= 1

            if portfolio_state.position_units > 0:
                bars_since_entry += 1
            else:
                bars_since_entry = 0

            current_equity = total_equity(portfolio_state, price)
            current_position_value = position_value(portfolio_state, price)
            current_allocation = allocation_pct(portfolio_state, price)
            save_portfolio_state(portfolio_state, cooldown_remaining, in_trend_mode=in_trend_mode)

            append_trade_history(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "pair": PAIR,
                    "price": price,
                    "signal": signal,
                    "signal_reason": decision.reason,
                    "strategy_name": ACTIVE_STRATEGY.name,
                    "strategy_config": ACTIVE_STRATEGY.to_dict()["config"],
                    "strategy_debug": debug,
                    "portfolio_config": PORTFOLIO_CONFIG.to_dict(),
                    "history_size": len(price_history),
                    "live_trading_enabled": LIVE_TRADING_ENABLED,
                    "strategy_in_position": portfolio_state.position_units > 0,
                    "strategy_entry_price": portfolio_state.average_entry_price,
                    "strategy_cooldown_remaining": cooldown_remaining,
                    "strategy_bars_since_entry": bars_since_entry,
                    "strategy_in_trend_mode": in_trend_mode,
                    "portfolio_state": portfolio_state.to_dict(),
                    "portfolio_equity": current_equity,
                    "portfolio_position_value": current_position_value,
                    "portfolio_allocation_pct": current_allocation * 100,
                    "buy_fraction_pct_multiplier": buy_fraction_pct_multiplier if signal == "BUY" else None,
                    "wallet": current_wallet,
                    "wallet_change": wallet_change,
                    "order": order_result,
                }
            )
            if order_result.get("status") in {"dry_run", "placed"}:
                append_execution_history(
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "pair": PAIR,
                        "price": price,
                        "signal": signal,
                        "signal_reason": decision.reason,
                        "strategy_name": ACTIVE_STRATEGY.name,
                        "strategy_debug": debug,
                        "portfolio_config": PORTFOLIO_CONFIG.to_dict(),
                        "portfolio_state": portfolio_state.to_dict(),
                        "portfolio_equity": current_equity,
                        "portfolio_position_value": current_position_value,
                        "portfolio_allocation_pct": current_allocation * 100,
                        "strategy_in_trend_mode": in_trend_mode,
                        "buy_fraction_pct_multiplier": buy_fraction_pct_multiplier if signal == "BUY" else None,
                        "wallet": current_wallet,
                        "wallet_change": wallet_change,
                        "order": order_result,
                    }
                )
        except (RoostooAPIError, ValueError) as exc:
            logger.exception("Bot cycle failed: %s", exc)
            append_trade_history(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "pair": PAIR,
                    "error": str(exc),
                    "error_type": exc.__class__.__name__,
                }
            )
        except KeyboardInterrupt:
            logger.info("Bot stopped by user.")
            raise
        except Exception as exc:
            logger.exception("Unexpected error: %s", exc)
            append_trade_history(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "pair": PAIR,
                    "error": str(exc),
                    "error_type": exc.__class__.__name__,
                }
            )

        time.sleep(POLL_INTERVAL_SECONDS)


def run_multi_asset_bot() -> None:
    configure_logging()
    client = RoostooClient()
    histories = {
        pair: deque(maxlen=max(PRICE_HISTORY_SIZE, ACTIVE_STRATEGY.required_history + 5))
        for pair in CONFIGURED_PAIRS
    }
    starting_wallet: Optional[Dict[str, float]] = None
    portfolio_state, pair_states = load_shared_portfolio_state()

    for pair in CONFIGURED_PAIRS:
        pair_states.setdefault(pair, {"cooldown_remaining": 0, "bars_since_entry": 0, "in_trend_mode": False})

    logger.info("Starting Roostoo trading bot for pairs %s", CONFIGURED_PAIRS)
    logger.info("Live trading enabled: %s", LIVE_TRADING_ENABLED)
    logger.info("Active strategy: %s", ACTIVE_STRATEGY.name)
    logger.info("Strategy config: %s", ACTIVE_STRATEGY.config)
    logger.info("Portfolio config: %s", PORTFOLIO_CONFIG)

    while True:
        try:
            balance_response = client.get_balance()
            current_wallet = summarize_wallet(extract_spot_wallet(balance_response))
            if starting_wallet is None:
                starting_wallet = load_starting_wallet()
            if starting_wallet is None:
                starting_wallet = current_wallet.copy()
                save_starting_wallet(starting_wallet)

            prices_by_pair: Dict[str, float] = {}
            for pair in CONFIGURED_PAIRS:
                ticker = client.get_ticker(pair)
                prices_by_pair[pair] = extract_price(ticker, pair)
                histories[pair].append(prices_by_pair[pair])

            if portfolio_state is None:
                portfolio_state = initialize_shared_portfolio_state(current_wallet, prices_by_pair)
            sync_shared_portfolio_with_wallet(portfolio_state, current_wallet, prices_by_pair)

            wallet_change = calculate_wallet_change(starting_wallet, current_wallet)
            pair_decisions: Dict[str, Dict[str, Any]] = {}
            for pair in CONFIGURED_PAIRS:
                price = prices_by_pair[pair]
                position = get_pair_position_state(portfolio_state, pair)
                pair_state = pair_states[pair]
                current_equity = shared_total_equity(portfolio_state, prices_by_pair)
                current_allocation = shared_allocation_pct(portfolio_state, pair, prices_by_pair)

                decision = evaluate_strategy(
                    list(histories[pair]),
                    in_position=position.position_units > 0,
                    entry_price=position.average_entry_price,
                    cooldown_remaining=int(pair_state["cooldown_remaining"]),
                    bars_since_entry=int(pair_state["bars_since_entry"]),
                    strategy=ACTIVE_STRATEGY,
                    position_context={"in_trend_mode": bool(pair_state.get("in_trend_mode", False))},
                )
                pair_state["in_trend_mode"] = bool(decision.debug.get("in_trend_mode", False))
                logger.info(
                    "Pair: %s | Latest price: %.8f | Signal: %s | Reason: %s | Debug: %s | Position units: %.8f | Shared cash: %.2f | Shared equity: %.2f | Pair allocation: %.2f%% | Cooldown: %d",
                    pair,
                    price,
                    decision.signal,
                    decision.reason,
                    json.dumps(decision.debug, sort_keys=True, default=str),
                    position.position_units,
                    portfolio_state.cash_balance,
                    current_equity,
                    current_allocation * 100,
                    int(pair_state["cooldown_remaining"]),
                )
                pair_decisions[pair] = {
                    "price": price,
                    "position": position,
                    "pair_state": pair_state,
                    "decision": decision,
                }

            ranking_results = compute_pair_ranking_results(
                {
                    pair: (list(histories[pair]), pair_decisions[pair]["decision"].debug)
                    for pair in pair_decisions
                    if pair_decisions[pair]["decision"].signal == "BUY"
                },
                PORTFOLIO_CONFIG,
            )

            pair_outputs: Dict[str, Dict[str, Any]] = {
                pair: {
                    "order_result": {"status": "skipped", "reason": "hold", "pair": pair},
                    "buy_fraction_pct_multiplier": None,
                    "volatility_buy_fraction_multiplier": None,
                    "pair_rank_multiplier": ranking_results.get(pair, {}).get("multiplier"),
                    "pair_ranking_score": ranking_results.get(pair, {}).get("score"),
                }
                for pair in CONFIGURED_PAIRS
            }

            for pair in CONFIGURED_PAIRS:
                decision = pair_decisions[pair]["decision"]
                if decision.signal != "SELL":
                    continue

                price = pair_decisions[pair]["price"]
                pair_state = pair_decisions[pair]["pair_state"]
                order_result = maybe_place_shared_order(
                    client,
                    pair,
                    decision.signal,
                    price,
                    portfolio_state,
                    prices_by_pair,
                    PORTFOLIO_CONFIG,
                    buy_fraction_pct_override=decision.buy_fraction_pct_override,
                    buy_fraction_pct_multiplier=None,
                )
                pair_outputs[pair]["order_result"] = order_result

                if order_result.get("status") in {"dry_run", "placed"}:
                    if get_pair_position_state(portfolio_state, pair).position_units <= POSITION_EPSILON:
                        pair_state["bars_since_entry"] = 0
                        pair_state["in_trend_mode"] = False
                        pair_state["cooldown_remaining"] = ACTIVE_STRATEGY.config.cooldown_periods

            buy_pairs = sorted(
                (
                    pair
                    for pair in CONFIGURED_PAIRS
                    if pair_decisions[pair]["decision"].signal == "BUY"
                ),
                key=lambda pair: (
                    -ranking_results.get(pair, {}).get("score", 0.0),
                    pair,
                ),
            )
            if PORTFOLIO_CONFIG.max_ranked_buys_per_bar > 0:
                buy_pairs = buy_pairs[: PORTFOLIO_CONFIG.max_ranked_buys_per_bar]
            for pair in buy_pairs:
                price = pair_decisions[pair]["price"]
                decision = pair_decisions[pair]["decision"]
                pair_state = pair_decisions[pair]["pair_state"]
                volatility_buy_fraction_multiplier = compute_buy_fraction_multiplier(
                    list(histories[pair]),
                    PORTFOLIO_CONFIG,
                )
                pair_rank_multiplier = ranking_results.get(pair, {}).get("multiplier", 1.0)
                pair_ranking_score = ranking_results.get(pair, {}).get("score", 0.0)
                buy_fraction_pct_multiplier = volatility_buy_fraction_multiplier * pair_rank_multiplier

                order_result = maybe_place_shared_order(
                    client,
                    pair,
                    decision.signal,
                    price,
                    portfolio_state,
                    prices_by_pair,
                    PORTFOLIO_CONFIG,
                    buy_fraction_pct_override=decision.buy_fraction_pct_override,
                    buy_fraction_pct_multiplier=buy_fraction_pct_multiplier,
                )
                pair_outputs[pair] = {
                    "order_result": order_result,
                    "buy_fraction_pct_multiplier": buy_fraction_pct_multiplier,
                    "volatility_buy_fraction_multiplier": volatility_buy_fraction_multiplier,
                    "pair_rank_multiplier": pair_rank_multiplier,
                    "pair_ranking_score": pair_ranking_score,
                }

                if order_result.get("status") in {"dry_run", "placed"}:
                    pair_state["cooldown_remaining"] = 0

            for pair in CONFIGURED_PAIRS:
                decision = pair_decisions[pair]["decision"]
                pair_state = pair_decisions[pair]["pair_state"]
                if decision.signal not in {"BUY", "SELL"} and int(pair_state["cooldown_remaining"]) > 0:
                    pair_state["cooldown_remaining"] = int(pair_state["cooldown_remaining"]) - 1

            for pair in CONFIGURED_PAIRS:
                pair_state = pair_decisions[pair]["pair_state"]
                if get_pair_position_state(portfolio_state, pair).position_units > 0:
                    pair_state["bars_since_entry"] = int(pair_state["bars_since_entry"]) + 1
                else:
                    pair_state["bars_since_entry"] = 0
                    pair_state["in_trend_mode"] = False

            for pair in CONFIGURED_PAIRS:
                price = pair_decisions[pair]["price"]
                decision = pair_decisions[pair]["decision"]
                pair_state = pair_decisions[pair]["pair_state"]
                output = pair_outputs[pair]
                current_equity = shared_total_equity(portfolio_state, prices_by_pair)
                current_position_value = shared_position_value(portfolio_state, pair, price)
                current_allocation = shared_allocation_pct(portfolio_state, pair, prices_by_pair)

                append_trade_history(
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "pair": pair,
                        "all_pairs": CONFIGURED_PAIRS,
                        "price": price,
                        "signal": decision.signal,
                        "signal_reason": decision.reason,
                        "strategy_name": ACTIVE_STRATEGY.name,
                        "strategy_config": ACTIVE_STRATEGY.to_dict()["config"],
                        "strategy_debug": decision.debug,
                        "portfolio_config": PORTFOLIO_CONFIG.to_dict(),
                        "history_size": len(histories[pair]),
                        "live_trading_enabled": LIVE_TRADING_ENABLED,
                        "strategy_in_position": get_pair_position_state(portfolio_state, pair).position_units
                        > 0,
                        "strategy_entry_price": get_pair_position_state(portfolio_state, pair).average_entry_price,
                        "strategy_cooldown_remaining": int(pair_state["cooldown_remaining"]),
                        "strategy_bars_since_entry": int(pair_state["bars_since_entry"]),
                        "strategy_in_trend_mode": bool(pair_state.get("in_trend_mode", False)),
                        "portfolio_state": portfolio_state.to_dict(),
                        "pair_position_state": get_pair_position_state(portfolio_state, pair).to_dict(),
                        "portfolio_equity": current_equity,
                        "pair_position_value": current_position_value,
                        "pair_allocation_pct": current_allocation * 100,
                        "buy_fraction_pct_multiplier": output["buy_fraction_pct_multiplier"],
                        "volatility_buy_fraction_multiplier": output["volatility_buy_fraction_multiplier"],
                        "pair_rank_multiplier": output["pair_rank_multiplier"],
                        "pair_ranking_score": output["pair_ranking_score"],
                        "wallet": current_wallet,
                        "wallet_change": wallet_change,
                        "order": output["order_result"],
                    }
                )
                if output["order_result"].get("status") in {"dry_run", "placed"}:
                    append_execution_history(
                        {
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "pair": pair,
                            "all_pairs": CONFIGURED_PAIRS,
                            "price": price,
                            "signal": decision.signal,
                            "signal_reason": decision.reason,
                            "strategy_name": ACTIVE_STRATEGY.name,
                            "strategy_debug": decision.debug,
                            "portfolio_config": PORTFOLIO_CONFIG.to_dict(),
                            "portfolio_state": portfolio_state.to_dict(),
                            "pair_position_state": get_pair_position_state(portfolio_state, pair).to_dict(),
                            "portfolio_equity": current_equity,
                            "pair_position_value": current_position_value,
                            "pair_allocation_pct": current_allocation * 100,
                            "strategy_in_trend_mode": bool(pair_state.get("in_trend_mode", False)),
                            "buy_fraction_pct_multiplier": output["buy_fraction_pct_multiplier"],
                            "volatility_buy_fraction_multiplier": output["volatility_buy_fraction_multiplier"],
                            "pair_rank_multiplier": output["pair_rank_multiplier"],
                            "pair_ranking_score": output["pair_ranking_score"],
                            "wallet": current_wallet,
                            "wallet_change": wallet_change,
                            "order": output["order_result"],
                        }
                    )

            save_shared_portfolio_state(portfolio_state, pair_states)
        except (RoostooAPIError, ValueError) as exc:
            logger.exception("Bot cycle failed: %s", exc)
            append_trade_history(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "pairs": CONFIGURED_PAIRS,
                    "error": str(exc),
                    "error_type": exc.__class__.__name__,
                }
            )
        except KeyboardInterrupt:
            logger.info("Bot stopped by user.")
            raise
        except Exception as exc:
            logger.exception("Unexpected error: %s", exc)
            append_trade_history(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "pairs": CONFIGURED_PAIRS,
                    "error": str(exc),
                    "error_type": exc.__class__.__name__,
                }
            )

        time.sleep(POLL_INTERVAL_SECONDS)


def run_bot() -> None:
    if len(CONFIGURED_PAIRS) > 1:
        run_multi_asset_bot()
        return

    run_single_asset_bot()


def calculate_wallet_change(
    starting_wallet: Optional[Dict[str, float]],
    current_wallet: Dict[str, float],
) -> Dict[str, float]:
    if starting_wallet is None:
        return {}

    changes: Dict[str, float] = {}
    assets = set(starting_wallet) | set(current_wallet)

    for asset in assets:
        changes[asset] = current_wallet.get(asset, 0.0) - starting_wallet.get(asset, 0.0)

    return changes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Roostoo trading bot")
    parser.add_argument(
        "--balance",
        action="store_true",
        help="Print the current spot wallet and exit.",
    )
    parser.add_argument(
        "--pnl",
        action="store_true",
        help="Print wallet change since the recorded starting balance and exit.",
    )
    parser.add_argument(
        "--backtest",
        metavar="CSV_PATH",
        nargs="+",
        help="Run an offline backtest using one or more Binance kline CSV files.",
    )
    parser.add_argument(
        "--backtest-trades-out",
        metavar="OUTPUT_PATH",
        help="Optional JSONL file path for exporting detailed backtest trade records.",
    )
    parser.add_argument(
        "--backtest-trades-csv-out",
        metavar="OUTPUT_PATH",
        help="Optional CSV file path for exporting detailed backtest trade records.",
    )
    parser.add_argument(
        "--backtest-timeseries-csv-out",
        metavar="OUTPUT_PATH",
        help="Optional CSV file path for exporting one backtest row per timestamp.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.balance:
        print_balance()
    elif args.pnl:
        print_pnl()
    elif args.backtest:
        print_backtest(
            args.backtest,
            args.backtest_trades_out,
            args.backtest_trades_csv_out,
            args.backtest_timeseries_csv_out,
        )
    else:
        run_bot()
