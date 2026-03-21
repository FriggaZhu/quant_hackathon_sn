import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from backtest import PriceBar, load_price_bars
from strategy import Strategy, build_strategy_from_env, evaluate_strategy


@dataclass
class SignalPoint:
    timestamp: object
    price: float
    signal: str
    reason: str


def load_dotenv(dotenv_path: str = ".env") -> None:
    env_file = Path(dotenv_path)
    if not env_file.exists():
        return

    for raw_line in env_file.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key:
            os.environ.setdefault(key, value)


def build_strategy() -> Strategy:
    load_dotenv()
    return build_strategy_from_env()


def replay_strategy(
    bars: List[PriceBar],
    strategy: Strategy,
) -> Dict[str, object]:
    timestamps: List[object] = []
    closes: List[float] = []
    ema_fast_values: List[Optional[float]] = []
    ema_slow_values: List[Optional[float]] = []
    ema_regime_values: List[Optional[float]] = []
    score_values: List[Optional[float]] = []
    rsi_values: List[Optional[float]] = []
    trailing_stop_values: List[Optional[float]] = []
    regime_values: List[int] = []
    buy_points: List[SignalPoint] = []
    sell_points: List[SignalPoint] = []

    recent_closes: List[float] = []
    in_position = False
    entry_price: Optional[float] = None
    cooldown_remaining = 0
    bars_since_entry = 0

    for bar in bars:
        recent_closes.append(bar.close)
        decision = evaluate_strategy(
            recent_closes,
            in_position=in_position,
            entry_price=entry_price,
            cooldown_remaining=cooldown_remaining,
            bars_since_entry=bars_since_entry,
            strategy=strategy,
        )
        debug = decision.debug

        timestamps.append(bar.timestamp)
        closes.append(bar.close)
        ema_fast_values.append(debug.get("ema9") or debug.get("ema_fast"))
        ema_slow_values.append(debug.get("ema21") or debug.get("ema_slow"))
        ema_regime_values.append(debug.get("ema50") or debug.get("ema_trend"))
        score_values.append(debug.get("score"))
        rsi_values.append(debug.get("rsi"))
        trailing_stop_values.append(debug.get("trailing_stop_level"))
        regime_values.append(1 if debug.get("regime_risk_on") else 0)

        if decision.signal == "BUY":
            buy_points.append(
                SignalPoint(
                    timestamp=bar.timestamp,
                    price=bar.close,
                    signal=decision.signal,
                    reason=decision.reason,
                )
            )
            if not in_position:
                in_position = True
                entry_price = bar.close
            cooldown_remaining = 0
        elif decision.signal == "SELL" and in_position:
            sell_points.append(
                SignalPoint(
                    timestamp=bar.timestamp,
                    price=bar.close,
                    signal=decision.signal,
                    reason=decision.reason,
                )
            )
            in_position = False
            entry_price = None
            bars_since_entry = 0
            cooldown_remaining = strategy.config.cooldown_periods
        elif cooldown_remaining > 0:
            cooldown_remaining -= 1

        if in_position:
            bars_since_entry += 1
        else:
            bars_since_entry = 0

    return {
        "timestamps": timestamps,
        "closes": closes,
        "ema_fast_values": ema_fast_values,
        "ema_slow_values": ema_slow_values,
        "ema_regime_values": ema_regime_values,
        "score_values": score_values,
        "rsi_values": rsi_values,
        "trailing_stop_values": trailing_stop_values,
        "regime_values": regime_values,
        "buy_points": buy_points,
        "sell_points": sell_points,
    }


def _plot_signal_markers(axis: plt.Axes, points: List[SignalPoint], color: str, marker: str) -> None:
    if not points:
        return

    axis.scatter(
        [point.timestamp for point in points],
        [point.price for point in points],
        color=color,
        marker=marker,
        s=90,
        zorder=5,
    )


def create_plot(
    replay: Dict[str, object],
    strategy: Strategy,
    csv_path: str,
    output_path: Optional[str],
) -> None:
    timestamps = replay["timestamps"]
    closes = replay["closes"]
    ema_fast_values = replay["ema_fast_values"]
    ema_slow_values = replay["ema_slow_values"]
    ema_regime_values = replay["ema_regime_values"]
    score_values = replay["score_values"]
    rsi_values = replay["rsi_values"]
    trailing_stop_values = replay["trailing_stop_values"]
    buy_points = replay["buy_points"]
    sell_points = replay["sell_points"]

    figure, (price_axis, score_axis) = plt.subplots(
        2,
        1,
        figsize=(15, 10),
        sharex=True,
        gridspec_kw={"height_ratios": [3, 1.6]},
    )

    price_axis.plot(timestamps, closes, label="Close", color="#1f2937", linewidth=1.2)
    if any(value is not None for value in ema_fast_values):
        price_axis.plot(
            timestamps,
            ema_fast_values,
            label="Fast EMA",
            color="#2563eb",
            linewidth=1.0,
        )
    if any(value is not None for value in ema_slow_values):
        price_axis.plot(
            timestamps,
            ema_slow_values,
            label="Slow EMA",
            color="#d97706",
            linewidth=1.0,
        )
    if any(value is not None for value in ema_regime_values):
        price_axis.plot(
            timestamps,
            ema_regime_values,
            label="Regime EMA",
            color="#7c3aed",
            linewidth=1.0,
        )
    price_axis.plot(
        timestamps,
        trailing_stop_values,
        label="Trailing Stop",
        color="#ef4444",
        linewidth=0.9,
        linestyle="--",
        alpha=0.8,
    )

    _plot_signal_markers(price_axis, buy_points, color="#16a34a", marker="^")
    _plot_signal_markers(price_axis, sell_points, color="#dc2626", marker="v")

    price_axis.set_title(f"Strategy Replay: {Path(csv_path).name} ({strategy.name})")
    price_axis.set_ylabel("Price")
    price_axis.legend(loc="upper left")
    price_axis.grid(alpha=0.2)

    if any(value is not None for value in score_values):
        score_axis.plot(timestamps, score_values, label="Composite Score", color="#0f766e", linewidth=1.1)
    if any(value is not None for value in rsi_values):
        score_axis.plot(timestamps, rsi_values, label="RSI", color="#9333ea", linewidth=1.0, alpha=0.8)

    strategy_config = strategy.config
    if hasattr(strategy_config, "entry_score_threshold"):
        score_axis.axhline(
            getattr(strategy_config, "entry_score_threshold"),
            color="#16a34a",
            linestyle="--",
            linewidth=0.9,
            label="Entry Threshold",
        )
    if hasattr(strategy_config, "exit_score_threshold"):
        score_axis.axhline(
            getattr(strategy_config, "exit_score_threshold"),
            color="#dc2626",
            linestyle="--",
            linewidth=0.9,
            label="Exit Threshold",
        )
    if hasattr(strategy_config, "rsi_entry_threshold"):
        score_axis.axhline(
            getattr(strategy_config, "rsi_entry_threshold"),
            color="#16a34a",
            linestyle="--",
            linewidth=0.9,
            label="RSI Entry Threshold",
        )
    if hasattr(strategy_config, "rsi_exit_threshold"):
        score_axis.axhline(
            getattr(strategy_config, "rsi_exit_threshold"),
            color="#dc2626",
            linestyle="--",
            linewidth=0.9,
            label="RSI Exit Threshold",
        )
    score_axis.set_ylabel("Score / RSI")
    score_axis.set_xlabel("Time")
    score_axis.legend(loc="upper left")
    score_axis.grid(alpha=0.2)

    score_axis.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
    score_axis.xaxis.set_major_locator(mdates.AutoDateLocator())
    figure.autofmt_xdate()
    figure.tight_layout()

    if output_path:
        figure.savefig(output_path, dpi=160, bbox_inches="tight")
        print(f"Saved plot to {output_path}")
    else:
        plt.show()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plot backtest buy/sell points for a CSV file.")
    parser.add_argument("csv_path", help="Path to the Binance kline CSV file.")
    parser.add_argument(
        "--output",
        help="Optional output image path. If omitted, the plot opens in a window.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    strategy = build_strategy()
    bars = load_price_bars(args.csv_path)
    replay = replay_strategy(bars, strategy)
    create_plot(replay, strategy, args.csv_path, args.output)


if __name__ == "__main__":
    main()
