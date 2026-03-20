import argparse
import json
import logging
import os
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from api import RoostooAPIError, RoostooClient
from backtest import load_price_bars, run_backtest
from strategy import StrategyConfig, evaluate_strategy


PAIR = os.getenv("ROOSTOO_PAIR", "BTC/USD")
ORDER_QUANTITY = float(os.getenv("ROOSTOO_ORDER_QUANTITY", "1"))
POLL_INTERVAL_SECONDS = int(os.getenv("ROOSTOO_POLL_INTERVAL", "60"))
PRICE_HISTORY_SIZE = int(os.getenv("ROOSTOO_HISTORY_SIZE", "20"))
LIVE_TRADING_ENABLED = os.getenv("ROOSTOO_ENABLE_TRADING", "false").lower() == "true"
LOG_DIR = Path(os.getenv("ROOSTOO_LOG_DIR", "logs"))
BOT_LOG_FILE = LOG_DIR / "bot.log"
TRADE_HISTORY_FILE = LOG_DIR / "trade_history.jsonl"
STARTING_BALANCE_FILE = LOG_DIR / "starting_wallet.json"
BACKTEST_INITIAL_CASH = float(os.getenv("ROOSTOO_BACKTEST_INITIAL_CASH", "10000"))
BACKTEST_FEE_RATE = float(os.getenv("ROOSTOO_BACKTEST_FEE_RATE", "0.001"))
STRATEGY_CONFIG = StrategyConfig(
    fast_ema_period=int(os.getenv("ROOSTOO_FAST_EMA_PERIOD", "9")),
    slow_ema_period=int(os.getenv("ROOSTOO_SLOW_EMA_PERIOD", "21")),
    rsi_period=int(os.getenv("ROOSTOO_RSI_PERIOD", "14")),
    rsi_buy_threshold=float(os.getenv("ROOSTOO_RSI_BUY_THRESHOLD", "55")),
    rsi_sell_threshold=float(os.getenv("ROOSTOO_RSI_SELL_THRESHOLD", "45")),
    min_ema_gap_pct=float(os.getenv("ROOSTOO_MIN_EMA_GAP_PCT", "0")),
    stop_loss_pct=float(os.getenv("ROOSTOO_STOP_LOSS_PCT", "0.015")),
    take_profit_pct=float(os.getenv("ROOSTOO_TAKE_PROFIT_PCT", "0.03")),
    cooldown_periods=int(os.getenv("ROOSTOO_COOLDOWN_PERIODS", "3")),
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


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


def extract_price(ticker_response: Dict[str, Any]) -> float:
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

        pair_data = data.get(PAIR)
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


def maybe_place_order(client: RoostooClient, signal: str) -> Dict[str, Any]:
    if signal not in {"BUY", "SELL"}:
        logger.info("Decision is HOLD. No order placed.")
        return {"status": "skipped", "reason": "hold"}

    if not LIVE_TRADING_ENABLED:
        logger.info(
            "Trading disabled. Would place %s order for %s %s.",
            signal,
            ORDER_QUANTITY,
            PAIR,
        )
        return {
            "status": "dry_run",
            "side": signal,
            "quantity": ORDER_QUANTITY,
            "pair": PAIR,
        }

    result = client.place_order(pair=PAIR, side=signal, quantity=ORDER_QUANTITY)
    logger.info("Order placed successfully: %s", result)
    return {
        "status": "placed",
        "side": signal,
        "quantity": ORDER_QUANTITY,
        "pair": PAIR,
        "response": result,
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


def print_backtest(csv_path: str) -> None:
    bars = load_price_bars(csv_path)
    summary = run_backtest(
        bars,
        starting_cash=BACKTEST_INITIAL_CASH,
        fee_rate=BACKTEST_FEE_RATE,
        strategy_config=STRATEGY_CONFIG,
    )
    summary["csv_path"] = csv_path
    summary["pair"] = PAIR
    print(json.dumps(summary, indent=2, sort_keys=True))


def run_bot() -> None:
    configure_logging()
    client = RoostooClient()
    history_size = max(PRICE_HISTORY_SIZE, STRATEGY_CONFIG.required_history + 5)
    price_history = deque(maxlen=history_size)
    starting_wallet: Optional[Dict[str, float]] = None
    in_position = False
    entry_price: Optional[float] = None
    cooldown_remaining = 0

    logger.info("Starting Roostoo trading bot for %s", PAIR)
    logger.info("Live trading enabled: %s", LIVE_TRADING_ENABLED)
    logger.info("Strategy config: %s", STRATEGY_CONFIG)

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
            price = extract_price(ticker)
            price_history.append(price)
            wallet_change = calculate_wallet_change(starting_wallet, current_wallet)

            decision = evaluate_strategy(
                list(price_history),
                in_position=in_position,
                entry_price=entry_price,
                cooldown_remaining=cooldown_remaining,
                config=STRATEGY_CONFIG,
            )
            signal = decision.signal
            logger.info(
                "Latest price: %.8f | Signal: %s | Reason: %s | EMA%d: %s | EMA%d: %s | RSI: %s | In position: %s | Cooldown: %d",
                price,
                signal,
                decision.reason,
                STRATEGY_CONFIG.fast_ema_period,
                f"{decision.fast_ema:.8f}" if decision.fast_ema is not None else "n/a",
                STRATEGY_CONFIG.slow_ema_period,
                f"{decision.slow_ema:.8f}" if decision.slow_ema is not None else "n/a",
                f"{decision.rsi:.2f}" if decision.rsi is not None else "n/a",
                in_position,
                cooldown_remaining,
            )
            logger.info("Spot wallet: %s", current_wallet)
            logger.info("Wallet change since start: %s", wallet_change)

            order_result = maybe_place_order(client, signal)
            if signal == "BUY" and order_result.get("status") in {"dry_run", "placed"}:
                in_position = True
                entry_price = price
                cooldown_remaining = 0
            elif signal == "SELL" and order_result.get("status") in {"dry_run", "placed"}:
                in_position = False
                entry_price = None
                cooldown_remaining = STRATEGY_CONFIG.cooldown_periods
            elif cooldown_remaining > 0:
                cooldown_remaining -= 1

            append_trade_history(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "pair": PAIR,
                    "price": price,
                    "signal": signal,
                    "signal_reason": decision.reason,
                    "fast_ema": decision.fast_ema,
                    "slow_ema": decision.slow_ema,
                    "rsi": decision.rsi,
                    "history_size": len(price_history),
                    "live_trading_enabled": LIVE_TRADING_ENABLED,
                    "strategy_in_position": in_position,
                    "strategy_entry_price": entry_price,
                    "strategy_cooldown_remaining": cooldown_remaining,
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
        help="Run an offline backtest using a Binance kline CSV or timestamp,close CSV.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.balance:
        print_balance()
    elif args.pnl:
        print_pnl()
    elif args.backtest:
        print_backtest(args.backtest)
    else:
        run_bot()
