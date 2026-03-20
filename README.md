# Roostoo Trading Bot (Beginner Version)

Simple autonomous crypto trading bot for the Roostoo Mock Exchange.

## Overview

This project is a starter bot built for learning:

- REST API integration
- request signing with HMAC SHA256
- a basic automated trading loop
- simple strategy wiring and logging

The bot:

- fetches market data from the Roostoo API
- fetches spot wallet balance from the Roostoo API
- stores recent prices in memory
- applies a simple momentum strategy
- decides `BUY`, `SELL`, or `HOLD`
- optionally places mock market orders
- logs every loop iteration

## Project Structure

- `main.py`: main loop, logging, price extraction, trade execution
- `api.py`: API client, request signing, GET/POST helpers
- `strategy.py`: simple momentum strategy

## Setup

Install the dependency:

```bash
pip install requests
```

Create a local `.env` file and fill in your real values:

```text
ROOSTOO_API_KEY=your_api_key
ROOSTOO_SECRET_KEY=your_secret_key
ROOSTOO_PAIR=BTC/USD
ROOSTOO_ORDER_QUANTITY=1
ROOSTOO_POLL_INTERVAL=300
ROOSTOO_HISTORY_SIZE=100
ROOSTOO_ENABLE_TRADING=false
```

The app now loads `.env` automatically when it starts, so you do not need to run `export ...` every time.

`ROOSTOO_ENABLE_TRADING=false` keeps the bot in dry-run mode so it logs intended trades without sending orders.

For the EMA/RSI strategy, a 5-minute polling interval is a reasonable default in live mode:

```text
ROOSTOO_POLL_INTERVAL=300
ROOSTOO_HISTORY_SIZE=100
ROOSTOO_FAST_EMA_PERIOD=9
ROOSTOO_SLOW_EMA_PERIOD=21
ROOSTOO_RSI_PERIOD=14
ROOSTOO_RSI_BUY_THRESHOLD=55
ROOSTOO_RSI_SELL_THRESHOLD=45
ROOSTOO_MIN_EMA_GAP_PCT=0
ROOSTOO_STOP_LOSS_PCT=0.015
ROOSTOO_TAKE_PROFIT_PCT=0.03
ROOSTOO_COOLDOWN_PERIODS=3
```

## Run

```bash
python main.py
```

Useful one-off commands:

```bash
python main.py --balance
python main.py --pnl
```

While it runs, the bot now writes:

- `logs/bot.log`: readable runtime log
- `logs/trade_history.jsonl`: one JSON record per cycle with timestamp, price, signal, wallet snapshot, wallet change, and order result
- `logs/starting_wallet.json`: the baseline wallet used for simple PnL comparison

You can watch the latest entries with:

```bash
tail -f logs/bot.log
```

## Backtesting With Historical Data

You can now run the current strategy against historical CSV data without calling the Roostoo API.

Supported inputs:

- Binance Vision kline CSV downloads, using the `close` column
- simple two-column CSV files in the format `timestamp,close`

Example:

```bash
python main.py --backtest data/BTCUSDT-1h.csv
```

Backtest settings come from `.env`:

```text
ROOSTOO_BACKTEST_INITIAL_CASH=10000
ROOSTOO_BACKTEST_FEE_RATE=0.001
```

The backtest currently simulates a simple spot account:

- starts in cash
- buys with the full cash balance on a `BUY` signal
- sells the full position on a `SELL` signal
- applies a fee on each buy and sell

This is intentionally lightweight so you can validate strategy behavior first before adding more advanced metrics.

## Strategy

Current strategy: long-only EMA/RSI trend following

- entry requires `EMA(9)` to cross above `EMA(21)` and RSI to be above the buy threshold
- entries can also require a minimum percentage gap between the fast and slow EMAs
- exit happens on stop loss, take profit, EMA cross down, or RSI weakness
- the bot holds at most one position at a time
- a cooldown delays re-entry after each sell to reduce churn

This is still intentionally simple, but it is much closer to a competition-ready spot strategy than the original one-step momentum rule.

## API Notes

Base URL:

```text
https://mock-api.roostoo.com
```

Headers used by the client:

- `RST-API-KEY`
- `MSG-SIGNATURE`
- `TIMESTAMP`

Signature method:

- HMAC SHA256
- payload is a sorted query-string-style serialization of request fields

Balance notes:

- the bot reads the balance endpoint with `GET /v3/balance`
- it expects the wallet field to be `SpotWallet`

## Safety Notes

- Start with dry-run mode
- Use the mock environment only
- Avoid reducing the polling interval aggressively
- This project does not yet include stop loss, exposure limits, or position tracking

## Future Improvements

- volatility filters
- persistent trade/performance logs
- stronger response validation
