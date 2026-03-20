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
from strategy import simple_momentum_signal


PAIR = os.getenv("ROOSTOO_PAIR", "BTC/USD")
ORDER_QUANTITY = float(os.getenv("ROOSTOO_ORDER_QUANTITY", "1"))
POLL_INTERVAL_SECONDS = int(os.getenv("ROOSTOO_POLL_INTERVAL", "60"))
PRICE_HISTORY_SIZE = int(os.getenv("ROOSTOO_HISTORY_SIZE", "20"))
LIVE_TRADING_ENABLED = os.getenv("ROOSTOO_ENABLE_TRADING", "false").lower() == "true"
LOG_DIR = Path(os.getenv("ROOSTOO_LOG_DIR", "logs"))
BOT_LOG_FILE = LOG_DIR / "bot.log"
TRADE_HISTORY_FILE = LOG_DIR / "trade_history.jsonl"
STARTING_BALANCE_FILE = LOG_DIR / "starting_wallet.json"

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


def run_bot() -> None:
    configure_logging()
    client = RoostooClient()
    price_history = deque(maxlen=PRICE_HISTORY_SIZE)
    starting_wallet: Optional[Dict[str, float]] = None

    logger.info("Starting Roostoo trading bot for %s", PAIR)
    logger.info("Live trading enabled: %s", LIVE_TRADING_ENABLED)

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

            signal = simple_momentum_signal(price_history)
            logger.info("Latest price: %.8f | Signal: %s", price, signal)
            logger.info("Spot wallet: %s", current_wallet)
            logger.info("Wallet change since start: %s", wallet_change)

            order_result = maybe_place_order(client, signal)
            append_trade_history(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "pair": PAIR,
                    "price": price,
                    "signal": signal,
                    "history_size": len(price_history),
                    "live_trading_enabled": LIVE_TRADING_ENABLED,
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
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.balance:
        print_balance()
    elif args.pnl:
        print_pnl()
    else:
        run_bot()
