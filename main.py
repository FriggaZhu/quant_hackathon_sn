import logging
import os
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
import json

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


def run_bot() -> None:
    configure_logging()
    client = RoostooClient()
    price_history = deque(maxlen=PRICE_HISTORY_SIZE)

    logger.info("Starting Roostoo trading bot for %s", PAIR)
    logger.info("Live trading enabled: %s", LIVE_TRADING_ENABLED)

    while True:
        try:
            ticker = client.get_ticker(PAIR)
            price = extract_price(ticker)
            price_history.append(price)

            signal = simple_momentum_signal(price_history)
            logger.info("Latest price: %.8f | Signal: %s", price, signal)

            order_result = maybe_place_order(client, signal)
            append_trade_history(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "pair": PAIR,
                    "price": price,
                    "signal": signal,
                    "history_size": len(price_history),
                    "live_trading_enabled": LIVE_TRADING_ENABLED,
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


if __name__ == "__main__":
    run_bot()
