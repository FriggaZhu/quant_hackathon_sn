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

Create a local `.env` file from the example:

```bash
cp .env.example .env
```

Then open `.env` and fill in your real values:

```text
ROOSTOO_API_KEY=your_api_key
ROOSTOO_SECRET_KEY=your_secret_key
ROOSTOO_PAIR=BTC/USD
ROOSTOO_ORDER_QUANTITY=1
ROOSTOO_POLL_INTERVAL=60
ROOSTOO_ENABLE_TRADING=false
```

The app now loads `.env` automatically when it starts, so you do not need to run `export ...` every time.

`ROOSTOO_ENABLE_TRADING=false` keeps the bot in dry-run mode so it logs intended trades without sending orders.

## Run

```bash
python main.py
```

While it runs, the bot now writes:

- `logs/bot.log`: readable runtime log
- `logs/trade_history.jsonl`: one JSON record per cycle with timestamp, price, signal, and order result

You can watch the latest entries with:

```bash
tail -f logs/bot.log
```

## Strategy

Current strategy: simple momentum

- if price increases, signal `BUY`
- if price decreases, signal `SELL`
- if price is unchanged or there is not enough history, signal `HOLD`

This is intentionally minimal and is meant for testing API integration and bot behavior, not for real trading performance.

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

## Safety Notes

- Start with dry-run mode
- Use the mock environment only
- Avoid reducing the polling interval aggressively
- This project does not yet include stop loss, exposure limits, or position tracking

## Future Improvements

- better momentum signals
- volatility filters
- risk management
- persistent trade/performance logs
- stronger response validation
