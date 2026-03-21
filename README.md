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
- applies a selectable trading strategy
- decides `BUY`, `SELL`, or `HOLD`
- optionally places mock market orders
- logs every loop iteration

## Project Structure

- `main.py`: main loop, logging, price extraction, trade execution
- `api.py`: API client, request signing, GET/POST helpers
- `strategy.py`: strategy framework, indicator helpers, and built-in strategies
- `portfolio.py`: position sizing, allocation limits, and partial execution helpers
- `backtest.py`: historical replay and performance summary
- `plot_backtest.py`: visual replay with buy/sell markers

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
ROOSTOO_PAIRS=BTC/USD,ETH/USD
ROOSTOO_POLL_INTERVAL=900
ROOSTOO_HISTORY_SIZE=100
ROOSTOO_ENABLE_TRADING=false
ROOSTOO_STRATEGY=mean_reversion
ROOSTOO_PORTFOLIO_MAX_ALLOCATION_PCT=0.20
ROOSTOO_PORTFOLIO_BUY_FRACTION_PCT=0.10
ROOSTOO_PORTFOLIO_SELL_FRACTION_PCT=0.75
ROOSTOO_PORTFOLIO_DUST_TRADE_NOTIONAL=50
```

The app now loads `.env` automatically when it starts, so you do not need to run `export ...` every time.

`ROOSTOO_ENABLE_TRADING=false` keeps the bot in dry-run mode so it logs intended trades without sending orders.

Switch strategies with one env var:

```text
ROOSTOO_STRATEGY=mean_reversion
ROOSTOO_STRATEGY=mtf_mean_reversion
ROOSTOO_STRATEGY=multi_factor
ROOSTOO_STRATEGY=regime_switch
```

To trade multiple assets with one shared cash pool, set:

```text
ROOSTOO_PAIRS=BTC/USD,ETH/USD
```

If `ROOSTOO_PAIRS` is set, the bot:

- keeps separate strategy state per pair
- shares one cash balance across all configured pairs
- enforces per-pair allocation limits using the shared portfolio
- logs per-pair executions into the same portfolio history

Recommended starting config for the mean-reversion strategy:

```text
ROOSTOO_POLL_INTERVAL=900
ROOSTOO_HISTORY_SIZE=100
ROOSTOO_STRATEGY=mean_reversion
ROOSTOO_MR_BOLLINGER_PERIOD=20
ROOSTOO_MR_BOLLINGER_STDDEV=2.0
ROOSTOO_MR_TREND_EMA_PERIOD=50
ROOSTOO_MR_RSI_PERIOD=14
ROOSTOO_MR_RSI_ENTRY_THRESHOLD=30
ROOSTOO_MR_WEAK_RSI_ENTRY_THRESHOLD=34
ROOSTOO_MR_RSI_EXIT_THRESHOLD=56
ROOSTOO_MR_MAX_TREND_DISTANCE_PCT=1.5
ROOSTOO_MR_STOP_LOSS_PCT=0.01
ROOSTOO_MR_TAKE_PROFIT_PCT=0.015
ROOSTOO_MR_COOLDOWN_PERIODS=2
ROOSTOO_MR_LOWER_BAND_ENTRY_BUFFER_PCT=0.003
ROOSTOO_MR_ALLOW_SCALE_IN=true
ROOSTOO_MR_MIN_DISTANCE_TO_MID_PCT=0.004
ROOSTOO_MR_MIDDLE_BAND_EXIT_BUFFER_PCT=0.002
ROOSTOO_MR_MINIMUM_HOLD_BARS=2
ROOSTOO_MR_STRONG_BUY_FRACTION_PCT=0.10
ROOSTOO_MR_WEAK_BUY_FRACTION_PCT=0.05
ROOSTOO_MR_VOLATILITY_PERIOD=20
ROOSTOO_MR_MAX_VOLATILITY=0.016
ROOSTOO_MR_TREND_EXTENSION_ENABLED=false
ROOSTOO_MR_TREND_EXTENSION_EMA_FAST_PERIOD=9
ROOSTOO_MR_TREND_EXTENSION_EMA_SLOW_PERIOD=21
ROOSTOO_MR_TREND_EXTENSION_RSI_THRESHOLD=55
ROOSTOO_MR_TREND_EXTENSION_TRAILING_STOP_PCT=0.012
ROOSTOO_MR_REQUIRE_PRICE_ABOVE_TREND_EMA_FOR_ENTRY=false
ROOSTOO_MR_ENTRY_BELOW_TREND_EMA_BUFFER_PCT=0.0
```

Alternative starting config for the multi-factor strategy:

```text
ROOSTOO_STRATEGY=multi_factor
ROOSTOO_EMA_FAST_PERIOD=9
ROOSTOO_EMA_SLOW_PERIOD=21
ROOSTOO_EMA_REGIME_PERIOD=50
ROOSTOO_EMA_SLOPE_PERIOD=20
ROOSTOO_RSI_PERIOD=14
ROOSTOO_VOLATILITY_PERIOD=20
ROOSTOO_ENTRY_SCORE_THRESHOLD=0.80
ROOSTOO_EXIT_SCORE_THRESHOLD=0.45
ROOSTOO_STOP_LOSS_PCT=0.012
ROOSTOO_TRAILING_STOP_PCT=0.015
ROOSTOO_MAX_VOLATILITY=0.016
ROOSTOO_COOLDOWN_PERIODS=12
ROOSTOO_PULLBACK_TOLERANCE_PCT=0.0035
ROOSTOO_REGIME_BREAK_BUFFER_PCT=0.01
ROOSTOO_USE_SCORE_EXIT=false
ROOSTOO_MIN_TREND_ENTRY_RSI=50
ROOSTOO_MIN_TREND_ENTRY_EMA_SLOPE=0.0
```

Regime-switch strategy:

```text
ROOSTOO_STRATEGY=regime_switch
ROOSTOO_REGIME_EMA_PERIOD=50
ROOSTOO_REGIME_SLOPE_EMA_PERIOD=20
ROOSTOO_REGIME_SLOPE_PERIOD=5
ROOSTOO_REGIME_VOLATILITY_PERIOD=20
ROOSTOO_REGIME_MIN_TREND_SLOPE=0.0
ROOSTOO_REGIME_FLAT_SLOPE_THRESHOLD=0.001
ROOSTOO_REGIME_TREND_FOLLOWING_MAX_VOLATILITY=0.016
ROOSTOO_REGIME_MEAN_REVERSION_MIN_VOLATILITY=0.007
ROOSTOO_REGIME_MEAN_REVERSION_MAX_VOLATILITY=0.016
ROOSTOO_REGIME_RISK_OFF_VOLATILITY=0.02
ROOSTOO_REGIME_MEAN_REVERSION_PRICE_DISTANCE_PCT=0.01
ROOSTOO_REGIME_MEAN_REVERSION_MAX_TREND_DISTANCE_PCT=2.0
ROOSTOO_REGIME_RISK_OFF_BREAK_BUFFER_PCT=0.01
ROOSTOO_REGIME_COOLDOWN_PERIODS=6
```

Multi-timeframe mean-reversion strategy:

```text
ROOSTOO_STRATEGY=mtf_mean_reversion
ROOSTOO_MTF_BASE_CANDLE_MINUTES=15
ROOSTOO_MTF_HOURLY_CANDLE_MINUTES=60
ROOSTOO_MTF_FOUR_HOUR_CANDLE_MINUTES=240
ROOSTOO_MTF_BOLLINGER_PERIOD=20
ROOSTOO_MTF_BOLLINGER_STDDEV=2.0
ROOSTOO_MTF_TREND_EMA_PERIOD=50
ROOSTOO_MTF_RSI_PERIOD=14
ROOSTOO_MTF_RSI_ENTRY_THRESHOLD=30
ROOSTOO_MTF_RSI_EXIT_THRESHOLD=52
ROOSTOO_MTF_MAX_TREND_DISTANCE_PCT=1.5
ROOSTOO_MTF_STOP_LOSS_PCT=0.01
ROOSTOO_MTF_TAKE_PROFIT_PCT=0.015
ROOSTOO_MTF_COOLDOWN_PERIODS=4
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

Multi-asset shared-portfolio example:

```bash
python main.py --backtest data/BTCUSDT-15m-2026-02.csv data/ETHUSDT-15m-2026-02.csv
```

Trade export example:

```bash
python main.py --backtest data/BTCUSDT-15m-2026-02.csv data/ETHUSDT-15m-2026-02.csv --backtest-trades-csv-out artifacts/backtests/exports/backtest_trades.csv
```

Per-timestamp portfolio state export example:

```bash
python main.py --backtest data/BTCUSDT-15m-2026-02.csv data/ETHUSDT-15m-2026-02.csv --backtest-timeseries-csv-out artifacts/backtests/exports/backtest_timeseries.csv
```

Runtime bot logs stay in `logs/`, but experiment outputs now go under `artifacts/`.

Every backtest run writes summary artifacts automatically to:

```text
artifacts/backtests/summary/backtest_latest_summary.json
artifacts/backtests/summary/backtest_runs.jsonl
artifacts/backtests/summary/backtest_runs.csv
artifacts/backtests/runs/backtest_summary_YYYYMMDD_HHMMSS_UTC.json
```

If you export detailed trades or per-timestamp CSVs, the exact output path is still written, and the run also stores a timestamped sibling copy such as:

```text
backtest_trades_YYYYMMDD_HHMMSS_UTC.csv
backtest_timeseries_YYYYMMDD_HHMMSS_UTC.csv
```

Strategy comparison sweeps can be logged the same way with the helper script:

```bash
python compare_strategies.py --backtest data/BTCUSDT-15m-2026-01.csv data/BTCUSDT-15m-2026-02.csv data/ETHUSDT-15m-2026-01.csv data/ETHUSDT-15m-2026-02.csv
```

For the current competition-style mean-reversion sweep on BTC+ETH+SOL:

```bash
python compare_strategies.py --dotenv .env.baseline_live_mean_reversion --preset mr_competition --backtest data/BTCUSDT-15m-2026-01.csv data/BTCUSDT-15m-2026-02.csv data/ETHUSDT-15m-2026-01.csv data/ETHUSDT-15m-2026-02.csv data/SOLUSDT-15m-2026-01.csv data/SOLUSDT-15m-2026-02.csv
```

The current `v2` preset writes:

```text
artifacts/compare/summary/latest_strategy_comparison_v2.json
artifacts/compare/summary/latest_strategy_comparison_v2.csv
artifacts/compare/timeseries/v2/
artifacts/compare/summary/strategy_compare_latest.json
artifacts/compare/summary/strategy_compare_runs.jsonl
artifacts/compare/summary/strategy_compare_runs.csv
artifacts/compare/runs/strategy_comparison_v2_YYYYMMDD_HHMMSS_UTC.json
artifacts/compare/runs/strategy_comparison_v2_YYYYMMDD_HHMMSS_UTC.csv
artifacts/compare/runs/v2_YYYYMMDD_HHMMSS_UTC/
```

To freeze the current tuned mean-reversion fallback before more experiments, keep a baseline env snapshot such as:

```text
.env.baseline_live_mean_reversion
```

To inspect which entry conditions are losing, run the trade-quality dashboard helper against a backtest trade CSV:

```bash
python analyze_trade_quality.py logs/backtest_trades.csv --output-dir artifacts/analysis/current_trade_quality
```

This writes bucketed summaries such as:

```text
artifacts/analysis/current_trade_quality/trade_quality_summary.json
artifacts/analysis/current_trade_quality/trade_quality_by_pair.csv
artifacts/analysis/current_trade_quality/trade_quality_by_exit_reason.csv
artifacts/analysis/current_trade_quality/trade_quality_by_entry_rsi_bucket.csv
artifacts/analysis/current_trade_quality/trade_quality_by_entry_volatility_bucket.csv
```

Backtest settings come from `.env`:

```text
ROOSTOO_BACKTEST_INITIAL_CASH=10000
ROOSTOO_BACKTEST_FEE_RATE=0.001
ROOSTOO_PORTFOLIO_MAX_ALLOCATION_PCT=0.20
ROOSTOO_PORTFOLIO_BUY_FRACTION_PCT=0.10
ROOSTOO_PORTFOLIO_SELL_FRACTION_PCT=0.75
ROOSTOO_PORTFOLIO_DUST_TRADE_NOTIONAL=50
ROOSTOO_BACKTEST_OUTPUT_DIR=artifacts/backtests
ROOSTOO_COMPARE_OUTPUT_DIR=artifacts/compare
```

The backtest now simulates a lightweight spot portfolio:

- starts in cash
- buys a configurable fraction of equity on a `BUY` signal
- caps exposure with a max allocation per asset
- sells a configurable fraction of the current position on a `SELL` signal
- applies a fee on each buy and sell
- tracks realized PnL, ending position value, and fees paid

This keeps the model simple, but it is much closer to how you would manage a larger competition portfolio than an all-in/all-out switch.

## Strategy

Current strategy: long-only Bollinger/RSI mean reversion

- entry looks for an oversold bounce back above the lower Bollinger Band
- it can also buy when price is very near the lower band instead of requiring a perfect bounce
- strong entries use the lower RSI threshold, while weaker entries can use a smaller secondary tier
- trades are skipped when the distance back to the middle band is too small to justify fees
- entries can require a minimum edge over assumed round-trip costs to reduce churn
- trades are skipped when price is too far from the longer-term trend EMA
- exit happens on mean reversion to the middle band plus a small buffer, a later RSI recovery, stop loss, or take profit
- the bot can scale into the position in multiple tranches when repeated dip signals appear
- a minimum hold period prevents immediate churn right after entry
- a cooldown delays re-entry after each sell to reduce churn

Built-in strategies:

- `mean_reversion`: Bollinger/RSI pullback entries with simple exits
- `mtf_mean_reversion`: mean-reversion entries gated by derived 1h and 4h trend filters
- `multi_factor`: regime-aware trend-pullback scoring model
- `regime_switch`: switches between trend-following and mean-reversion based on trend slope and volatility

The framework is designed so new strategies can be added without changing the live bot or backtest runner.

## Portfolio Sizing

The bot now separates signal generation from execution sizing.

- strategies still output only `BUY`, `SELL`, or `HOLD`
- the portfolio layer decides how much to trade
- `BUY` opens a tranche instead of spending the full wallet
- `SELL` can trim part of the position instead of fully exiting

Useful portfolio settings:

```text
ROOSTOO_PORTFOLIO_MAX_ALLOCATION_PCT=0.20
ROOSTOO_PORTFOLIO_BUY_FRACTION_PCT=0.10
ROOSTOO_PORTFOLIO_SELL_FRACTION_PCT=0.75
ROOSTOO_PORTFOLIO_MIN_TRADE_NOTIONAL=0
ROOSTOO_PORTFOLIO_DUST_TRADE_NOTIONAL=50
ROOSTOO_PORTFOLIO_USE_VOLATILITY_SCALING=true
ROOSTOO_PORTFOLIO_VOLATILITY_PERIOD=20
ROOSTOO_PORTFOLIO_TARGET_VOLATILITY=0.012
ROOSTOO_PORTFOLIO_MIN_BUY_FRACTION_MULTIPLIER=0.50
ROOSTOO_PORTFOLIO_MAX_BUY_FRACTION_MULTIPLIER=1.25
ROOSTOO_PORTFOLIO_USE_PAIR_RANKING=true
ROOSTOO_PORTFOLIO_PAIR_RANKING_LOOKBACK=48
ROOSTOO_PORTFOLIO_MIN_PAIR_RANK_MULTIPLIER=0.85
ROOSTOO_PORTFOLIO_MAX_PAIR_RANK_MULTIPLIER=1.15
ROOSTOO_PORTFOLIO_MAX_RANKED_BUYS_PER_BAR=0
```

Example interpretation:

- max allocation `0.20`: never let this pair exceed 20% of portfolio equity
- buy fraction `0.10`: each buy signal opens about a 10% equity tranche
- volatility scaling reduces tranche size when realized volatility is high and allows modestly larger tranches when volatility is calmer
- pair ranking boosts the strongest BTC/ETH opportunity and trims the weaker one when both want to buy at the same time
- max ranked buys per bar `1`: only the top-ranked simultaneous buy is allowed to open on that bar
- sell fraction `0.75`: each sell signal closes most of the active position and reduces churn

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
