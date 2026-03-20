from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass
class StrategyConfig:
    fast_ema_period: int = 9
    slow_ema_period: int = 21
    rsi_period: int = 14
    rsi_buy_threshold: float = 55.0
    rsi_sell_threshold: float = 45.0
    min_ema_gap_pct: float = 0.0
    stop_loss_pct: float = 0.015
    take_profit_pct: float = 0.03
    cooldown_periods: int = 3

    @property
    def required_history(self) -> int:
        return max(self.slow_ema_period + 1, self.rsi_period + 1)


@dataclass
class StrategyDecision:
    signal: str
    reason: str
    fast_ema: Optional[float]
    slow_ema: Optional[float]
    rsi: Optional[float]


def calculate_ema(prices: Sequence[float], period: int) -> Optional[float]:
    if period <= 0 or len(prices) < period:
        return None

    multiplier = 2 / (period + 1)
    ema = sum(prices[:period]) / period

    for price in prices[period:]:
        ema = ((price - ema) * multiplier) + ema

    return ema


def calculate_rsi(prices: Sequence[float], period: int) -> Optional[float]:
    if period <= 0 or len(prices) < period + 1:
        return None

    gains = 0.0
    losses = 0.0
    recent_prices = prices[-(period + 1) :]

    for previous_price, current_price in zip(recent_prices, recent_prices[1:]):
        change = current_price - previous_price
        if change > 0:
            gains += change
        elif change < 0:
            losses -= change

    average_gain = gains / period
    average_loss = losses / period

    if average_loss == 0:
        if average_gain == 0:
            return 50.0
        return 100.0

    relative_strength = average_gain / average_loss
    return 100 - (100 / (1 + relative_strength))


def evaluate_strategy(
    prices: Sequence[float],
    in_position: bool,
    entry_price: Optional[float],
    cooldown_remaining: int,
    config: Optional[StrategyConfig] = None,
) -> StrategyDecision:
    config = config or StrategyConfig()
    if len(prices) < config.required_history:
        return StrategyDecision(
            signal="HOLD",
            reason="waiting_for_history",
            fast_ema=None,
            slow_ema=None,
            rsi=None,
        )

    current_price = prices[-1]
    current_fast_ema = calculate_ema(prices, config.fast_ema_period)
    current_slow_ema = calculate_ema(prices, config.slow_ema_period)
    previous_fast_ema = calculate_ema(prices[:-1], config.fast_ema_period)
    previous_slow_ema = calculate_ema(prices[:-1], config.slow_ema_period)
    current_rsi = calculate_rsi(prices, config.rsi_period)

    if (
        current_fast_ema is None
        or current_slow_ema is None
        or previous_fast_ema is None
        or previous_slow_ema is None
        or current_rsi is None
    ):
        return StrategyDecision(
            signal="HOLD",
            reason="indicators_unavailable",
            fast_ema=current_fast_ema,
            slow_ema=current_slow_ema,
            rsi=current_rsi,
        )

    if in_position and entry_price is not None:
        stop_price = entry_price * (1 - config.stop_loss_pct)
        take_profit_price = entry_price * (1 + config.take_profit_pct)

        if current_price <= stop_price:
            return StrategyDecision(
                signal="SELL",
                reason="stop_loss",
                fast_ema=current_fast_ema,
                slow_ema=current_slow_ema,
                rsi=current_rsi,
            )

        if current_price >= take_profit_price:
            return StrategyDecision(
                signal="SELL",
                reason="take_profit",
                fast_ema=current_fast_ema,
                slow_ema=current_slow_ema,
                rsi=current_rsi,
            )

        if previous_fast_ema >= previous_slow_ema and current_fast_ema < current_slow_ema:
            return StrategyDecision(
                signal="SELL",
                reason="ema_cross_down",
                fast_ema=current_fast_ema,
                slow_ema=current_slow_ema,
                rsi=current_rsi,
            )

        if current_rsi <= config.rsi_sell_threshold:
            return StrategyDecision(
                signal="SELL",
                reason="rsi_exit",
                fast_ema=current_fast_ema,
                slow_ema=current_slow_ema,
                rsi=current_rsi,
            )

        return StrategyDecision(
            signal="HOLD",
            reason="holding_position",
            fast_ema=current_fast_ema,
            slow_ema=current_slow_ema,
            rsi=current_rsi,
        )

    if cooldown_remaining > 0:
        return StrategyDecision(
            signal="HOLD",
            reason="cooldown",
            fast_ema=current_fast_ema,
            slow_ema=current_slow_ema,
            rsi=current_rsi,
        )

    crossed_up = previous_fast_ema <= previous_slow_ema and current_fast_ema > current_slow_ema
    ema_gap_pct = ((current_fast_ema - current_slow_ema) / current_slow_ema) * 100
    if (
        crossed_up
        and current_rsi >= config.rsi_buy_threshold
        and ema_gap_pct >= config.min_ema_gap_pct
    ):
        return StrategyDecision(
            signal="BUY",
            reason="ema_cross_up_with_rsi_gap",
            fast_ema=current_fast_ema,
            slow_ema=current_slow_ema,
            rsi=current_rsi,
        )

    return StrategyDecision(
        signal="HOLD",
        reason="no_entry",
        fast_ema=current_fast_ema,
        slow_ema=current_slow_ema,
        rsi=current_rsi,
    )


def simple_momentum_signal(prices: Sequence[float]) -> str:
    if len(prices) < 2:
        return "HOLD"

    previous_price = prices[-2]
    current_price = prices[-1]

    if current_price > previous_price:
        return "BUY"
    if current_price < previous_price:
        return "SELL"
    return "HOLD"
