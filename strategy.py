import os
from dataclasses import asdict, dataclass, field
from math import sqrt
from typing import Any, Callable, Dict, Optional, Sequence, Union


@dataclass
class StrategyDecision:
    signal: str
    reason: str
    debug: Dict[str, Any] = field(default_factory=dict)
    buy_fraction_pct_override: Optional[float] = None


@dataclass
class MeanReversionConfig:
    bollinger_period: int = 20
    bollinger_stddev: float = 2.0
    trend_ema_period: int = 50
    rsi_period: int = 14
    rsi_entry_threshold: float = 30.0
    rsi_exit_threshold: float = 56.0
    max_trend_distance_pct: float = 1.5
    stop_loss_pct: float = 0.01
    take_profit_pct: float = 0.015
    cooldown_periods: int = 4
    lower_band_entry_buffer_pct: float = 0.003
    allow_scale_in: bool = True
    min_distance_to_mid_pct: float = 0.004
    middle_band_exit_buffer_pct: float = 0.002
    minimum_hold_bars: int = 2
    weak_rsi_entry_threshold: float = 34.0
    strong_buy_fraction_pct: float = 0.10
    weak_buy_fraction_pct: float = 0.05
    assumed_round_trip_cost_pct: float = 0.0025
    minimum_edge_to_cost_ratio: float = 1.5
    volatility_period: int = 20
    max_volatility: float = 0.016
    trend_extension_enabled: bool = False
    trend_extension_ema_fast_period: int = 9
    trend_extension_ema_slow_period: int = 21
    trend_extension_rsi_threshold: float = 55.0
    trend_extension_trailing_stop_pct: float = 0.012
    require_price_above_trend_ema_for_entry: bool = False
    entry_below_trend_ema_buffer_pct: float = 0.0

    @property
    def required_history(self) -> int:
        return max(
            self.bollinger_period + 1,
            self.trend_ema_period,
            self.rsi_period + 1,
            self.trend_extension_ema_slow_period + 1,
        )


@dataclass
class MultiFactorConfig:
    ema_fast_period: int = 9
    ema_slow_period: int = 21
    ema_regime_period: int = 50
    ema_slope_period: int = 20
    rsi_period: int = 14
    volatility_period: int = 20
    entry_score_threshold: float = 0.80
    exit_score_threshold: float = 0.45
    stop_loss_pct: float = 0.012
    trailing_stop_pct: float = 0.015
    max_volatility: float = 0.016
    cooldown_periods: int = 12
    pullback_tolerance_pct: float = 0.0035
    regime_break_buffer_pct: float = 0.01
    use_volatility_regime_filter: bool = True
    use_score_exit: bool = False
    minimum_entry_rsi: float = 50.0
    minimum_entry_ema_slope: float = 0.0
    trend_weight: float = 0.40
    pullback_weight: float = 0.25
    rsi_weight: float = 0.20
    volatility_penalty_weight: float = 0.15

    @property
    def required_history(self) -> int:
        return max(
            self.ema_regime_period + 2,
            self.ema_slow_period + 2,
            self.ema_slope_period + 2,
            self.rsi_period + 1,
            self.volatility_period + 1,
        )


@dataclass
class MultiTimeframeMeanReversionConfig:
    base_candle_minutes: int = 15
    hourly_candle_minutes: int = 60
    four_hour_candle_minutes: int = 240
    bollinger_period: int = 20
    bollinger_stddev: float = 2.0
    trend_ema_period: int = 50
    rsi_period: int = 14
    rsi_entry_threshold: float = 30.0
    rsi_exit_threshold: float = 52.0
    max_trend_distance_pct: float = 1.5
    stop_loss_pct: float = 0.01
    take_profit_pct: float = 0.015
    cooldown_periods: int = 4
    min_distance_to_mid_pct: float = 0.004
    assumed_round_trip_cost_pct: float = 0.0025
    minimum_edge_to_cost_ratio: float = 1.5

    @property
    def hourly_group_size(self) -> int:
        return max(1, self.hourly_candle_minutes // self.base_candle_minutes)

    @property
    def four_hour_group_size(self) -> int:
        return max(1, self.four_hour_candle_minutes // self.base_candle_minutes)

    @property
    def required_history(self) -> int:
        return max(
            self.bollinger_period + 1,
            self.rsi_period + 1,
            self.trend_ema_period * self.four_hour_group_size,
            self.trend_ema_period * self.hourly_group_size,
        )


@dataclass
class MultiTimeframeMeanReversionV2Config:
    base_candle_minutes: int = 5
    filter_candle_minutes: int = 15
    filter_bollinger_period: int = 20
    filter_bollinger_stddev: float = 2.0
    filter_trend_ema_period: int = 50
    filter_rsi_period: int = 14
    filter_volatility_period: int = 20
    filter_max_volatility: float = 0.013
    filter_min_distance_to_mid_pct: float = 0.009
    filter_entry_below_trend_ema_buffer_pct: float = 0.005
    exec_bollinger_period: int = 20
    exec_bollinger_stddev: float = 2.0
    exec_rsi_period: int = 14
    exec_rsi_entry_threshold: float = 28.0
    exec_rsi_exit_threshold: float = 58.0
    exec_volatility_period: int = 20
    exec_max_volatility: float = 0.010
    exec_lower_band_entry_buffer_pct: float = 0.002
    stop_loss_pct: float = 0.007
    take_profit_pct: float = 0.011
    cooldown_periods: int = 4
    minimum_hold_bars: int = 3
    strong_buy_fraction_pct: float = 0.08
    assumed_round_trip_cost_pct: float = 0.0025
    minimum_edge_to_cost_ratio: float = 1.5
    trend_extension_enabled: bool = True
    trend_extension_ema_fast_period: int = 9
    trend_extension_ema_slow_period: int = 21
    trend_extension_rsi_threshold: float = 55.0
    trend_extension_trailing_stop_pct: float = 0.009

    @property
    def filter_group_size(self) -> int:
        return max(1, self.filter_candle_minutes // self.base_candle_minutes)

    @property
    def required_history(self) -> int:
        return max(
            (self.filter_bollinger_period + 1) * self.filter_group_size,
            self.filter_trend_ema_period * self.filter_group_size,
            (self.filter_rsi_period + 1) * self.filter_group_size,
            (self.filter_volatility_period + 1) * self.filter_group_size,
            self.exec_bollinger_period + 1,
            self.exec_rsi_period + 1,
            self.exec_volatility_period + 1,
            self.trend_extension_ema_slow_period + 1,
        )


@dataclass
class RegimeSwitchConfig:
    regime_ema_period: int = 50
    regime_slope_ema_period: int = 20
    regime_slope_period: int = 5
    volatility_period: int = 20
    minimum_trend_slope: float = 0.0
    flat_slope_threshold: float = 0.001
    trend_following_max_volatility: float = 0.016
    mean_reversion_min_volatility: float = 0.007
    mean_reversion_max_volatility: float = 0.016
    risk_off_volatility: float = 0.02
    mean_reversion_price_distance_pct: float = 0.01
    mean_reversion_max_trend_distance_pct: float = 2.0
    risk_off_break_buffer_pct: float = 0.01
    cooldown_periods: int = 6
    mean_reversion_config: MeanReversionConfig = field(default_factory=MeanReversionConfig)
    multi_factor_config: MultiFactorConfig = field(default_factory=MultiFactorConfig)

    @property
    def required_history(self) -> int:
        return max(
            self.regime_ema_period + self.regime_slope_period,
            self.regime_slope_ema_period + self.regime_slope_period,
            self.volatility_period + 1,
            self.mean_reversion_config.required_history,
            self.multi_factor_config.required_history,
        )


StrategyConfig = Union[
    MeanReversionConfig,
    MultiFactorConfig,
    MultiTimeframeMeanReversionConfig,
    MultiTimeframeMeanReversionV2Config,
    RegimeSwitchConfig,
]
StrategyEvaluator = Callable[
    [Sequence[float], bool, Optional[float], int, int, Optional[Dict[str, Any]], StrategyConfig],
    StrategyDecision,
]


@dataclass
class Strategy:
    name: str
    config: StrategyConfig
    evaluator: StrategyEvaluator

    @property
    def required_history(self) -> int:
        return self.config.required_history

    def evaluate(
        self,
        recent_closes: Sequence[float],
        in_position: bool,
        entry_price: Optional[float],
        cooldown_remaining: int,
        bars_since_entry: int,
        position_context: Optional[Dict[str, Any]] = None,
    ) -> StrategyDecision:
        return self.evaluator(
            recent_closes,
            in_position,
            entry_price,
            cooldown_remaining,
            bars_since_entry,
            position_context,
            self.config,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "config": asdict(self.config)}


def ema(prices: Sequence[float], period: int) -> Optional[float]:
    if period <= 0 or len(prices) < period:
        return None

    multiplier = 2 / (period + 1)
    ema_value = sum(prices[:period]) / period

    for price in prices[period:]:
        ema_value = ((price - ema_value) * multiplier) + ema_value

    return ema_value


def rsi(prices: Sequence[float], period: int) -> Optional[float]:
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


def realized_volatility(prices: Sequence[float], period: int) -> Optional[float]:
    if period <= 1 or len(prices) < period + 1:
        return None

    window = prices[-(period + 1) :]
    returns = []
    for previous_price, current_price in zip(window, window[1:]):
        if previous_price <= 0:
            return None
        returns.append((current_price - previous_price) / previous_price)

    if not returns:
        return None

    mean_return = sum(returns) / len(returns)
    variance = sum((value - mean_return) ** 2 for value in returns) / len(returns)
    return sqrt(variance)


def ema_slope(prices: Sequence[float], ema_period: int, slope_period: int) -> Optional[float]:
    if slope_period <= 0 or len(prices) < ema_period + slope_period:
        return None

    current_ema = ema(prices, ema_period)
    previous_ema = ema(prices[:-slope_period], ema_period)
    if current_ema is None or previous_ema is None or previous_ema == 0:
        return None

    return (current_ema - previous_ema) / previous_ema


def trailing_stop_level(highest_price_since_entry: float, trailing_stop_pct: float) -> float:
    return highest_price_since_entry * (1 - trailing_stop_pct)


def bollinger_bands(
    prices: Sequence[float],
    period: int,
    stddev_multiplier: float,
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    if period <= 0 or len(prices) < period:
        return None, None, None

    window = prices[-period:]
    mean_price = sum(window) / period
    variance = sum((price - mean_price) ** 2 for price in window) / period
    standard_deviation = sqrt(variance)

    upper_band = mean_price + (standard_deviation * stddev_multiplier)
    lower_band = mean_price - (standard_deviation * stddev_multiplier)
    return mean_price, lower_band, upper_band


def aggregate_closes(prices: Sequence[float], group_size: int) -> list[float]:
    if group_size <= 1:
        return list(prices)

    aggregated: list[float] = []
    for index in range(group_size - 1, len(prices), group_size):
        aggregated.append(prices[index])
    return aggregated


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


def _to_bool(value: str, default: bool = True) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _required_edge_pct(min_distance_to_mid_pct: float, assumed_round_trip_cost_pct: float, ratio: float) -> float:
    return max(min_distance_to_mid_pct, assumed_round_trip_cost_pct * ratio)


def detect_regime(
    current_price: float,
    regime_ema: float,
    slope_ema_value: float,
    slope_value: float,
    realized_vol: float,
    *,
    trend_following_max_volatility: float,
    risk_off_volatility: float,
    flat_slope_threshold: float,
    mean_reversion_price_distance_pct: float,
    risk_off_break_buffer_pct: float,
) -> tuple[str, Dict[str, float]]:
    price_distance_to_ema_pct = abs((current_price - regime_ema) / regime_ema) if regime_ema else 0.0
    trend_regime = (
        current_price > regime_ema
        and slope_value > flat_slope_threshold
        and realized_vol <= trend_following_max_volatility
    )
    risk_off_regime = (
        realized_vol >= risk_off_volatility
        or (
            current_price < regime_ema * (1 - risk_off_break_buffer_pct)
            and slope_value < -flat_slope_threshold
        )
    )
    mean_reversion_regime = (
        not trend_regime
        and not risk_off_regime
        and price_distance_to_ema_pct <= mean_reversion_price_distance_pct
        and abs(slope_value) <= flat_slope_threshold
    )

    if risk_off_regime:
        regime = "RISK_OFF"
    elif trend_regime:
        regime = "TREND"
    elif mean_reversion_regime:
        regime = "MEAN_REVERSION"
    else:
        regime = "RISK_OFF"

    return regime, {
        "regime_ema": regime_ema,
        "slope_ema": slope_ema_value,
        "ema_slope": slope_value,
        "volatility": realized_vol,
        "price_distance_to_ema_pct": round(price_distance_to_ema_pct * 100, 4),
        "trend_regime": trend_regime,
        "mean_reversion_regime": mean_reversion_regime,
        "risk_off_regime": risk_off_regime,
    }


def _mean_reversion_evaluate(
    recent_closes: Sequence[float],
    in_position: bool,
    entry_price: Optional[float],
    cooldown_remaining: int,
    bars_since_entry: int,
    position_context: Optional[Dict[str, Any]],
    config: StrategyConfig,
) -> StrategyDecision:
    if not isinstance(config, MeanReversionConfig):
        raise TypeError("Mean reversion strategy requires MeanReversionConfig.")

    if len(recent_closes) < config.required_history:
        return StrategyDecision(
            signal="HOLD",
            reason="insufficient_data",
            debug={"history_length": len(recent_closes), "required_history": config.required_history},
        )

    current_price = recent_closes[-1]
    previous_price = recent_closes[-2]
    trend_ema_value = ema(recent_closes, config.trend_ema_period)
    trend_extension_ema_fast = ema(recent_closes, config.trend_extension_ema_fast_period)
    trend_extension_ema_slow = ema(recent_closes, config.trend_extension_ema_slow_period)
    rsi_value = rsi(recent_closes, config.rsi_period)
    volatility_value = realized_volatility(recent_closes, config.volatility_period)
    middle_band, lower_band, upper_band = bollinger_bands(
        recent_closes,
        config.bollinger_period,
        config.bollinger_stddev,
    )
    _, previous_lower_band, _ = bollinger_bands(
        recent_closes[:-1],
        config.bollinger_period,
        config.bollinger_stddev,
    )

    if (
        trend_ema_value is None
        or rsi_value is None
        or volatility_value is None
        or middle_band is None
        or lower_band is None
        or upper_band is None
        or previous_lower_band is None
    ):
        return StrategyDecision(
            signal="HOLD",
            reason="indicators_unavailable",
            debug={"history_length": len(recent_closes)},
        )

    trend_distance_pct = abs((current_price - trend_ema_value) / trend_ema_value) * 100
    bounced_from_lower_band = previous_price < previous_lower_band and current_price >= lower_band
    near_lower_band = current_price <= lower_band * (1 + config.lower_band_entry_buffer_pct)
    distance_to_mid_pct = ((middle_band - current_price) / current_price) if current_price > 0 else 0.0
    required_distance_to_mid_pct = _required_edge_pct(
        config.min_distance_to_mid_pct,
        config.assumed_round_trip_cost_pct,
        config.minimum_edge_to_cost_ratio,
    )
    strong_entry_ready = (
        (bounced_from_lower_band or near_lower_band)
        and rsi_value <= config.rsi_entry_threshold
    )
    weak_entry_ready = near_lower_band and rsi_value <= config.weak_rsi_entry_threshold
    previous_in_trend_mode = bool((position_context or {}).get("in_trend_mode", False))
    trend_trigger = (
        config.trend_extension_enabled
        and in_position
        and trend_extension_ema_fast is not None
        and trend_extension_ema_slow is not None
        and current_price > trend_extension_ema_slow
        and trend_extension_ema_fast > trend_extension_ema_slow
        and rsi_value > config.trend_extension_rsi_threshold
    )
    in_trend_mode = in_position and (previous_in_trend_mode or trend_trigger)

    debug = {
        "strategy_name": "mean_reversion",
        "current_price": current_price,
        "ema_trend": trend_ema_value,
        "middle_band": middle_band,
        "lower_band": lower_band,
        "upper_band": upper_band,
        "rsi": rsi_value,
        "volatility": volatility_value,
        "trend_distance_pct": round(trend_distance_pct, 4),
        "max_trend_distance_pct": config.max_trend_distance_pct,
        "distance_to_mid_pct": round(distance_to_mid_pct, 4),
        "required_distance_to_mid_pct": round(required_distance_to_mid_pct, 4),
        "assumed_round_trip_cost_pct": round(config.assumed_round_trip_cost_pct, 4),
        "minimum_edge_to_cost_ratio": config.minimum_edge_to_cost_ratio,
        "max_volatility": config.max_volatility,
        "bounced_from_lower_band": bounced_from_lower_band,
        "near_lower_band": near_lower_band,
        "strong_entry_ready": strong_entry_ready,
        "weak_entry_ready": weak_entry_ready,
        "bars_since_entry": bars_since_entry,
        "trend_extension_enabled": config.trend_extension_enabled,
        "trend_extension_ema_fast": trend_extension_ema_fast,
        "trend_extension_ema_slow": trend_extension_ema_slow,
        "trend_extension_rsi_threshold": config.trend_extension_rsi_threshold,
        "trend_trigger": trend_trigger,
        "previous_in_trend_mode": previous_in_trend_mode,
        "in_trend_mode": in_trend_mode,
    }

    if in_position and entry_price is not None:
        stop_price = entry_price * (1 - config.stop_loss_pct)
        take_profit_price = entry_price * (1 + config.take_profit_pct)
        trailing_stop_price = None
        if config.trend_extension_enabled and bars_since_entry >= 0:
            window_size = min(len(recent_closes), max(1, bars_since_entry + 1))
            highest_price_since_entry = max(recent_closes[-window_size:])
            trailing_stop_price = trailing_stop_level(
                highest_price_since_entry,
                config.trend_extension_trailing_stop_pct,
            )
        debug["stop_loss_level"] = stop_price
        debug["take_profit_level"] = take_profit_price
        debug["minimum_hold_bars"] = config.minimum_hold_bars
        debug["middle_band_exit_level"] = middle_band * (1 + config.middle_band_exit_buffer_pct)
        debug["trend_extension_trailing_stop_level"] = trailing_stop_price

        if current_price <= stop_price:
            return StrategyDecision("SELL", "stop_loss", debug)

        if config.trend_extension_enabled and in_trend_mode:
            if (
                trend_extension_ema_fast is not None
                and trend_extension_ema_slow is not None
                and trend_extension_ema_fast < trend_extension_ema_slow
            ):
                return StrategyDecision("SELL", "trend_extension_ema_fast_below_slow", debug)

            if trailing_stop_price is not None and current_price <= trailing_stop_price:
                return StrategyDecision("SELL", "trend_extension_trailing_stop_hit", debug)

            return StrategyDecision("HOLD", "holding_trend_extension", debug)

        if current_price >= take_profit_price:
            return StrategyDecision("SELL", "take_profit", debug)

        if bars_since_entry >= config.minimum_hold_bars and current_price >= middle_band * (
            1 + config.middle_band_exit_buffer_pct
        ):
            return StrategyDecision("SELL", "mean_reversion_exit", debug)

        if bars_since_entry >= config.minimum_hold_bars and rsi_value >= config.rsi_exit_threshold:
            return StrategyDecision("SELL", "rsi_exit", debug)

        if (
            config.allow_scale_in
            and cooldown_remaining <= 0
            and not in_trend_mode
            and trend_distance_pct <= config.max_trend_distance_pct
            and strong_entry_ready
        ):
            return StrategyDecision(
                "BUY",
                "scale_in_near_lower_band",
                debug,
                buy_fraction_pct_override=config.strong_buy_fraction_pct,
            )

        if (
            config.allow_scale_in
            and cooldown_remaining <= 0
            and not in_trend_mode
            and trend_distance_pct <= config.max_trend_distance_pct
            and not strong_entry_ready
            and weak_entry_ready
        ):
            return StrategyDecision(
                "BUY",
                "scale_in_weak_near_lower_band",
                debug,
                buy_fraction_pct_override=config.weak_buy_fraction_pct,
            )

        return StrategyDecision("HOLD", "holding_position", debug)

    if cooldown_remaining > 0:
        return StrategyDecision("HOLD", "cooldown", debug)

    if volatility_value > config.max_volatility:
        return StrategyDecision("HOLD", "volatility_too_high", debug)

    if (
        config.require_price_above_trend_ema_for_entry
        and current_price < trend_ema_value * (1 - config.entry_below_trend_ema_buffer_pct)
    ):
        return StrategyDecision("HOLD", "below_trend_ema_entry_filter", debug)

    if trend_distance_pct > config.max_trend_distance_pct:
        return StrategyDecision("HOLD", "too_far_from_trend", debug)

    if distance_to_mid_pct < required_distance_to_mid_pct:
        return StrategyDecision("HOLD", "insufficient_profit_potential", debug)

    if strong_entry_ready:
        if bounced_from_lower_band:
            return StrategyDecision(
                "BUY",
                "lower_band_bounce_with_rsi",
                debug,
                buy_fraction_pct_override=config.strong_buy_fraction_pct,
            )
        return StrategyDecision(
            "BUY",
            "near_lower_band_with_rsi",
            debug,
            buy_fraction_pct_override=config.strong_buy_fraction_pct,
        )

    if weak_entry_ready:
        return StrategyDecision(
            "BUY",
            "weak_near_lower_band_with_rsi",
            debug,
            buy_fraction_pct_override=config.weak_buy_fraction_pct,
        )

    return StrategyDecision("HOLD", "no_entry", debug)


def _multifactor_evaluate(
    recent_closes: Sequence[float],
    in_position: bool,
    entry_price: Optional[float],
    cooldown_remaining: int,
    bars_since_entry: int,
    position_context: Optional[Dict[str, Any]],
    config: StrategyConfig,
) -> StrategyDecision:
    if not isinstance(config, MultiFactorConfig):
        raise TypeError("Multi-factor strategy requires MultiFactorConfig.")

    if len(recent_closes) < config.required_history:
        return StrategyDecision(
            signal="HOLD",
            reason="insufficient_data",
            debug={"history_length": len(recent_closes), "required_history": config.required_history},
        )

    current_price = recent_closes[-1]
    ema_fast_value = ema(recent_closes, config.ema_fast_period)
    ema_slow_value = ema(recent_closes, config.ema_slow_period)
    ema_regime_value = ema(recent_closes, config.ema_regime_period)
    ema_slope_value = ema_slope(recent_closes, config.ema_slope_period, 1)
    rsi_value = rsi(recent_closes, config.rsi_period)
    volatility_value = realized_volatility(recent_closes, config.volatility_period)

    if (
        ema_fast_value is None
        or ema_slow_value is None
        or ema_regime_value is None
        or ema_slope_value is None
        or rsi_value is None
        or volatility_value is None
    ):
        return StrategyDecision(
            signal="HOLD",
            reason="indicators_unavailable",
            debug={"history_length": len(recent_closes)},
        )

    regime_risk_on = (
        current_price > ema_regime_value
        and ema_slope_value > config.minimum_entry_ema_slope
        and rsi_value > config.minimum_entry_rsi
    )
    if config.use_volatility_regime_filter:
        regime_risk_on = regime_risk_on and volatility_value <= config.max_volatility

    trend_score = 0.0
    if ema_fast_value > ema_slow_value:
        trend_score += 0.6
    if current_price > ema_slow_value:
        trend_score += 0.4
    trend_score = _clamp(trend_score)

    pullback_score = 0.0
    if current_price >= ema_slow_value:
        distance_from_fast = (ema_fast_value - current_price) / ema_fast_value if ema_fast_value else 0.0
        if 0 <= distance_from_fast <= config.pullback_tolerance_pct:
            pullback_score = 1.0 - (distance_from_fast / config.pullback_tolerance_pct)
            pullback_score = _clamp(pullback_score)

    rsi_score = 0.0
    if 35 <= rsi_value <= 55:
        rsi_score = 1.0
    elif 30 <= rsi_value < 35:
        rsi_score = 0.7
    elif 55 < rsi_value <= 60:
        rsi_score = 0.5
    elif 25 <= rsi_value < 30:
        rsi_score = 0.3

    volatility_penalty = 0.0
    if volatility_value > config.max_volatility:
        excess_volatility = (volatility_value - config.max_volatility) / config.max_volatility
        volatility_penalty = _clamp(excess_volatility)

    composite_score = (
        (trend_score * config.trend_weight)
        + (pullback_score * config.pullback_weight)
        + (rsi_score * config.rsi_weight)
        - (volatility_penalty * config.volatility_penalty_weight)
    )
    composite_score = _clamp(composite_score)

    trailing_stop = None
    if in_position and entry_price is not None:
        highest_price_since_entry = max(recent_closes[-config.cooldown_periods - 20 :])
        trailing_stop = trailing_stop_level(highest_price_since_entry, config.trailing_stop_pct)

    debug = {
        "strategy_name": "multi_factor",
        "current_price": current_price,
        "ema9": ema_fast_value,
        "ema21": ema_slow_value,
        "ema50": ema_regime_value,
        "ema_slope": ema_slope_value,
        "rsi": rsi_value,
        "volatility": volatility_value,
        "regime_risk_on": regime_risk_on,
        "trend_score": round(trend_score, 4),
        "pullback_score": round(pullback_score, 4),
        "rsi_score": round(rsi_score, 4),
        "volatility_penalty": round(volatility_penalty, 4),
        "score": round(composite_score, 4),
        "trailing_stop_level": trailing_stop,
        "score_exit_enabled": config.use_score_exit,
        "minimum_entry_rsi": config.minimum_entry_rsi,
        "minimum_entry_ema_slope": config.minimum_entry_ema_slope,
    }

    if in_position and entry_price is not None:
        stop_loss_level = entry_price * (1 - config.stop_loss_pct)
        debug["stop_loss_level"] = stop_loss_level

        if current_price <= stop_loss_level:
            return StrategyDecision("SELL", "stop_loss_hit", debug)

        if trailing_stop is not None and current_price <= trailing_stop:
            return StrategyDecision("SELL", "trailing_stop_hit", debug)

        if ema_fast_value < ema_slow_value:
            return StrategyDecision("SELL", "ema_fast_below_slow", debug)

        if config.use_score_exit and composite_score < config.exit_score_threshold:
            return StrategyDecision("SELL", "score_below_exit_threshold", debug)

        clearly_risk_off = (
            (
                current_price < ema_regime_value * (1 - config.regime_break_buffer_pct)
                and ema_slope_value < 0
            )
            or volatility_value > (config.max_volatility * 1.25)
        )
        if clearly_risk_off:
            return StrategyDecision("SELL", "regime_turned_risk_off", debug)

        return StrategyDecision("HOLD", "holding_position", debug)

    if cooldown_remaining > 0:
        return StrategyDecision("HOLD", "cooldown_active", debug)

    if not regime_risk_on:
        return StrategyDecision("HOLD", "regime_not_risk_on", debug)

    if composite_score >= config.entry_score_threshold:
        return StrategyDecision("BUY", "score_above_entry_threshold", debug)

    return StrategyDecision("HOLD", "entry_score_too_low", debug)


def _mtf_mean_reversion_evaluate(
    recent_closes: Sequence[float],
    in_position: bool,
    entry_price: Optional[float],
    cooldown_remaining: int,
    bars_since_entry: int,
    position_context: Optional[Dict[str, Any]],
    config: StrategyConfig,
) -> StrategyDecision:
    if not isinstance(config, MultiTimeframeMeanReversionConfig):
        raise TypeError("Multi-timeframe mean reversion strategy requires MultiTimeframeMeanReversionConfig.")

    if len(recent_closes) < config.required_history:
        return StrategyDecision(
            signal="HOLD",
            reason="insufficient_data",
            debug={"history_length": len(recent_closes), "required_history": config.required_history},
        )

    current_price = recent_closes[-1]
    previous_price = recent_closes[-2]

    hourly_closes = aggregate_closes(recent_closes, config.hourly_group_size)
    four_hour_closes = aggregate_closes(recent_closes, config.four_hour_group_size)
    hourly_ema = ema(hourly_closes, config.trend_ema_period)
    four_hour_ema = ema(four_hour_closes, config.trend_ema_period)

    if hourly_ema is None or four_hour_ema is None:
        return StrategyDecision(
            signal="HOLD",
            reason="insufficient_higher_timeframe_data",
            debug={
                "hourly_history": len(hourly_closes),
                "four_hour_history": len(four_hour_closes),
                "required_trend_period": config.trend_ema_period,
            },
        )

    trend_ema_value = ema(recent_closes, config.trend_ema_period)
    rsi_value = rsi(recent_closes, config.rsi_period)
    middle_band, lower_band, upper_band = bollinger_bands(
        recent_closes,
        config.bollinger_period,
        config.bollinger_stddev,
    )
    _, previous_lower_band, _ = bollinger_bands(
        recent_closes[:-1],
        config.bollinger_period,
        config.bollinger_stddev,
    )

    if (
        trend_ema_value is None
        or rsi_value is None
        or middle_band is None
        or lower_band is None
        or upper_band is None
        or previous_lower_band is None
    ):
        return StrategyDecision(
            signal="HOLD",
            reason="indicators_unavailable",
            debug={"history_length": len(recent_closes)},
        )

    one_hour_filter = current_price > hourly_ema
    four_hour_filter = current_price > four_hour_ema
    risk_on = one_hour_filter and four_hour_filter
    trend_distance_pct = abs((current_price - trend_ema_value) / trend_ema_value) * 100
    bounced_from_lower_band = previous_price < previous_lower_band and current_price >= lower_band
    distance_to_mid_pct = ((middle_band - current_price) / current_price) if current_price > 0 else 0.0
    required_distance_to_mid_pct = _required_edge_pct(
        config.min_distance_to_mid_pct,
        config.assumed_round_trip_cost_pct,
        config.minimum_edge_to_cost_ratio,
    )

    debug = {
        "strategy_name": "mtf_mean_reversion",
        "current_price": current_price,
        "ema_trend_15m": trend_ema_value,
        "ema_trend_1h": hourly_ema,
        "ema_trend_4h": four_hour_ema,
        "one_hour_filter": one_hour_filter,
        "four_hour_filter": four_hour_filter,
        "risk_on": risk_on,
        "middle_band": middle_band,
        "lower_band": lower_band,
        "upper_band": upper_band,
        "rsi": rsi_value,
        "trend_distance_pct": round(trend_distance_pct, 4),
        "distance_to_mid_pct": round(distance_to_mid_pct, 4),
        "required_distance_to_mid_pct": round(required_distance_to_mid_pct, 4),
        "assumed_round_trip_cost_pct": round(config.assumed_round_trip_cost_pct, 4),
        "minimum_edge_to_cost_ratio": config.minimum_edge_to_cost_ratio,
        "bounced_from_lower_band": bounced_from_lower_band,
    }

    if in_position and entry_price is not None:
        stop_price = entry_price * (1 - config.stop_loss_pct)
        take_profit_price = entry_price * (1 + config.take_profit_pct)
        debug["stop_loss_level"] = stop_price
        debug["take_profit_level"] = take_profit_price

        if current_price <= stop_price:
            return StrategyDecision("SELL", "stop_loss", debug)

        if current_price >= take_profit_price:
            return StrategyDecision("SELL", "take_profit", debug)

        if current_price >= middle_band:
            return StrategyDecision("SELL", "mean_reversion_exit", debug)

        if rsi_value >= config.rsi_exit_threshold:
            return StrategyDecision("SELL", "rsi_exit", debug)

        if not risk_on:
            return StrategyDecision("SELL", "higher_timeframe_filter_failed", debug)

        return StrategyDecision("HOLD", "holding_position", debug)

    if cooldown_remaining > 0:
        return StrategyDecision("HOLD", "cooldown", debug)

    if not risk_on:
        return StrategyDecision("HOLD", "higher_timeframe_filters_not_bullish", debug)

    if trend_distance_pct > config.max_trend_distance_pct:
        return StrategyDecision("HOLD", "too_far_from_trend", debug)

    if distance_to_mid_pct < required_distance_to_mid_pct:
        return StrategyDecision("HOLD", "insufficient_profit_potential", debug)

    if bounced_from_lower_band and rsi_value <= config.rsi_entry_threshold:
        return StrategyDecision("BUY", "mtf_lower_band_bounce_with_rsi", debug)

    return StrategyDecision("HOLD", "no_entry", debug)


def _mtf_mean_reversion_v2_evaluate(
    recent_closes: Sequence[float],
    in_position: bool,
    entry_price: Optional[float],
    cooldown_remaining: int,
    bars_since_entry: int,
    position_context: Optional[Dict[str, Any]],
    config: StrategyConfig,
) -> StrategyDecision:
    if not isinstance(config, MultiTimeframeMeanReversionV2Config):
        raise TypeError("MTF mean reversion v2 requires MultiTimeframeMeanReversionV2Config.")

    if len(recent_closes) < config.required_history:
        return StrategyDecision(
            signal="HOLD",
            reason="insufficient_data",
            debug={"history_length": len(recent_closes), "required_history": config.required_history},
        )

    current_price = recent_closes[-1]
    previous_price = recent_closes[-2]
    filter_closes = aggregate_closes(recent_closes, config.filter_group_size)
    if len(filter_closes) < max(
        config.filter_bollinger_period + 1,
        config.filter_trend_ema_period,
        config.filter_rsi_period + 1,
        config.filter_volatility_period + 1,
    ):
        return StrategyDecision(
            signal="HOLD",
            reason="insufficient_filter_data",
            debug={
                "history_length": len(recent_closes),
                "filter_history_length": len(filter_closes),
                "required_filter_history": max(
                    config.filter_bollinger_period + 1,
                    config.filter_trend_ema_period,
                    config.filter_rsi_period + 1,
                    config.filter_volatility_period + 1,
                ),
            },
        )

    filter_price = filter_closes[-1]
    filter_ema_value = ema(filter_closes, config.filter_trend_ema_period)
    filter_rsi_value = rsi(filter_closes, config.filter_rsi_period)
    filter_volatility_value = realized_volatility(filter_closes, config.filter_volatility_period)
    filter_middle_band, filter_lower_band, filter_upper_band = bollinger_bands(
        filter_closes,
        config.filter_bollinger_period,
        config.filter_bollinger_stddev,
    )

    exec_rsi_value = rsi(recent_closes, config.exec_rsi_period)
    exec_volatility_value = realized_volatility(recent_closes, config.exec_volatility_period)
    exec_middle_band, exec_lower_band, exec_upper_band = bollinger_bands(
        recent_closes,
        config.exec_bollinger_period,
        config.exec_bollinger_stddev,
    )
    _, previous_exec_lower_band, _ = bollinger_bands(
        recent_closes[:-1],
        config.exec_bollinger_period,
        config.exec_bollinger_stddev,
    )
    trend_extension_ema_fast = ema(recent_closes, config.trend_extension_ema_fast_period)
    trend_extension_ema_slow = ema(recent_closes, config.trend_extension_ema_slow_period)

    if (
        filter_ema_value is None
        or filter_rsi_value is None
        or filter_volatility_value is None
        or filter_middle_band is None
        or filter_lower_band is None
        or filter_upper_band is None
        or exec_rsi_value is None
        or exec_volatility_value is None
        or exec_middle_band is None
        or exec_lower_band is None
        or exec_upper_band is None
        or previous_exec_lower_band is None
        or trend_extension_ema_fast is None
        or trend_extension_ema_slow is None
    ):
        return StrategyDecision(
            signal="HOLD",
            reason="indicators_unavailable",
            debug={"history_length": len(recent_closes), "filter_history_length": len(filter_closes)},
        )

    filter_distance_to_mid_pct = ((filter_middle_band - filter_price) / filter_price) if filter_price > 0 else 0.0
    required_filter_distance_to_mid_pct = _required_edge_pct(
        config.filter_min_distance_to_mid_pct,
        config.assumed_round_trip_cost_pct,
        config.minimum_edge_to_cost_ratio,
    )
    filter_allow_trade = (
        filter_price >= filter_ema_value * (1 - config.filter_entry_below_trend_ema_buffer_pct)
        and filter_volatility_value <= config.filter_max_volatility
        and filter_distance_to_mid_pct >= required_filter_distance_to_mid_pct
    )
    filter_risk_off = (
        filter_volatility_value > config.filter_max_volatility
        or filter_price < filter_ema_value * (1 - config.filter_entry_below_trend_ema_buffer_pct)
    )

    bounced_from_exec_lower_band = previous_price < previous_exec_lower_band and current_price >= exec_lower_band
    near_exec_lower_band = current_price <= exec_lower_band * (1 + config.exec_lower_band_entry_buffer_pct)
    exec_entry_ready = (
        (bounced_from_exec_lower_band or near_exec_lower_band)
        and exec_rsi_value <= config.exec_rsi_entry_threshold
        and exec_volatility_value <= config.exec_max_volatility
    )

    previous_in_trend_mode = bool((position_context or {}).get("in_trend_mode", False))
    trend_trigger = (
        config.trend_extension_enabled
        and in_position
        and current_price > trend_extension_ema_slow
        and trend_extension_ema_fast > trend_extension_ema_slow
        and exec_rsi_value > config.trend_extension_rsi_threshold
    )
    in_trend_mode = in_position and (previous_in_trend_mode or trend_trigger)

    debug = {
        "strategy_name": "mtf_mean_reversion_v2",
        "base_candle_minutes": config.base_candle_minutes,
        "filter_candle_minutes": config.filter_candle_minutes,
        "filter_group_size": config.filter_group_size,
        "current_price_5m": current_price,
        "filter_price_15m": filter_price,
        "filter_allow_trade": filter_allow_trade,
        "filter_risk_off": filter_risk_off,
        "filter_ema_15m": filter_ema_value,
        "filter_middle_band_15m": filter_middle_band,
        "filter_lower_band_15m": filter_lower_band,
        "filter_upper_band_15m": filter_upper_band,
        "filter_rsi_15m": filter_rsi_value,
        "filter_volatility_15m": filter_volatility_value,
        "filter_distance_to_mid_pct": round(filter_distance_to_mid_pct, 4),
        "required_filter_distance_to_mid_pct": round(required_filter_distance_to_mid_pct, 4),
        "exec_middle_band_5m": exec_middle_band,
        "exec_lower_band_5m": exec_lower_band,
        "exec_upper_band_5m": exec_upper_band,
        "exec_rsi_5m": exec_rsi_value,
        "exec_volatility_5m": exec_volatility_value,
        "exec_max_volatility": config.exec_max_volatility,
        "bounced_from_exec_lower_band": bounced_from_exec_lower_band,
        "near_exec_lower_band": near_exec_lower_band,
        "exec_entry_ready": exec_entry_ready,
        "bars_since_entry": bars_since_entry,
        "trend_extension_ema_fast_5m": trend_extension_ema_fast,
        "trend_extension_ema_slow_5m": trend_extension_ema_slow,
        "trend_extension_rsi_threshold": config.trend_extension_rsi_threshold,
        "trend_trigger": trend_trigger,
        "previous_in_trend_mode": previous_in_trend_mode,
        "in_trend_mode": in_trend_mode,
    }

    if in_position and entry_price is not None:
        stop_price = entry_price * (1 - config.stop_loss_pct)
        take_profit_price = entry_price * (1 + config.take_profit_pct)
        window_size = min(len(recent_closes), max(1, bars_since_entry + 1))
        highest_price_since_entry = max(recent_closes[-window_size:])
        trailing_stop_price = trailing_stop_level(
            highest_price_since_entry,
            config.trend_extension_trailing_stop_pct,
        )
        debug["stop_loss_level"] = stop_price
        debug["take_profit_level"] = take_profit_price
        debug["minimum_hold_bars"] = config.minimum_hold_bars
        debug["middle_band_exit_level"] = exec_middle_band
        debug["trend_extension_trailing_stop_level"] = trailing_stop_price

        if current_price <= stop_price:
            return StrategyDecision("SELL", "stop_loss", debug)

        if filter_risk_off:
            return StrategyDecision("SELL", "higher_timeframe_risk_off_exit", debug)

        if config.trend_extension_enabled and in_trend_mode:
            if trend_extension_ema_fast < trend_extension_ema_slow:
                return StrategyDecision("SELL", "trend_extension_ema_fast_below_slow", debug)

            if current_price <= trailing_stop_price:
                return StrategyDecision("SELL", "trend_extension_trailing_stop_hit", debug)

            return StrategyDecision("HOLD", "holding_trend_extension", debug)

        if current_price >= take_profit_price:
            return StrategyDecision("SELL", "take_profit", debug)

        if bars_since_entry >= config.minimum_hold_bars and current_price >= exec_middle_band:
            return StrategyDecision("SELL", "mean_reversion_exit", debug)

        if bars_since_entry >= config.minimum_hold_bars and exec_rsi_value >= config.exec_rsi_exit_threshold:
            return StrategyDecision("SELL", "rsi_exit", debug)

        return StrategyDecision("HOLD", "holding_position", debug)

    if cooldown_remaining > 0:
        return StrategyDecision("HOLD", "cooldown", debug)

    if not filter_allow_trade:
        return StrategyDecision("HOLD", "filter_trade_not_allowed", debug)

    if exec_volatility_value > config.exec_max_volatility:
        return StrategyDecision("HOLD", "exec_volatility_too_high", debug)

    if not exec_entry_ready:
        return StrategyDecision("HOLD", "no_entry", debug)

    if bounced_from_exec_lower_band:
        return StrategyDecision(
            "BUY",
            "5m_lower_band_bounce_with_15m_filter",
            debug,
            buy_fraction_pct_override=config.strong_buy_fraction_pct,
        )

    return StrategyDecision(
        "BUY",
        "5m_near_lower_band_with_15m_filter",
        debug,
        buy_fraction_pct_override=config.strong_buy_fraction_pct,
    )


def _regime_switch_evaluate(
    recent_closes: Sequence[float],
    in_position: bool,
    entry_price: Optional[float],
    cooldown_remaining: int,
    bars_since_entry: int,
    position_context: Optional[Dict[str, Any]],
    config: StrategyConfig,
) -> StrategyDecision:
    if not isinstance(config, RegimeSwitchConfig):
        raise TypeError("Regime switch strategy requires RegimeSwitchConfig.")

    if len(recent_closes) < config.required_history:
        return StrategyDecision(
            signal="HOLD",
            reason="insufficient_data",
            debug={"history_length": len(recent_closes), "required_history": config.required_history},
        )

    current_price = recent_closes[-1]
    regime_ema_value = ema(recent_closes, config.regime_ema_period)
    slope_ema_value = ema(recent_closes, config.regime_slope_ema_period)
    regime_slope_value = ema_slope(recent_closes, config.regime_slope_ema_period, config.regime_slope_period)
    volatility_value = realized_volatility(recent_closes, config.volatility_period)

    if (
        regime_ema_value is None
        or slope_ema_value is None
        or regime_slope_value is None
        or volatility_value is None
    ):
        return StrategyDecision(
            signal="HOLD",
            reason="indicators_unavailable",
            debug={"history_length": len(recent_closes)},
        )

    regime_name, regime_debug = detect_regime(
        current_price,
        regime_ema_value,
        slope_ema_value,
        regime_slope_value,
        volatility_value,
        trend_following_max_volatility=config.trend_following_max_volatility,
        risk_off_volatility=config.risk_off_volatility,
        flat_slope_threshold=max(config.flat_slope_threshold, config.minimum_trend_slope),
        mean_reversion_price_distance_pct=config.mean_reversion_price_distance_pct,
        risk_off_break_buffer_pct=config.risk_off_break_buffer_pct,
    )
    trend_regime = regime_name == "TREND"
    mean_reversion_regime = (
        regime_name == "MEAN_REVERSION"
        and current_price >= regime_ema_value
        and volatility_value >= config.mean_reversion_min_volatility
        and volatility_value <= config.mean_reversion_max_volatility
        and regime_debug["price_distance_to_ema_pct"] <= config.mean_reversion_max_trend_distance_pct
    )
    risk_off_regime = regime_name == "RISK_OFF"

    debug = {
        "strategy_name": "regime_switch",
        "current_price": current_price,
        "regime_ema": regime_ema_value,
        "slope_ema": slope_ema_value,
        "regime_slope": regime_slope_value,
        "volatility": volatility_value,
        "detected_regime": regime_name,
        "trend_regime": trend_regime,
        "mean_reversion_regime": mean_reversion_regime,
        "risk_off_regime": risk_off_regime,
        **regime_debug,
    }

    if risk_off_regime:
        if in_position:
            return StrategyDecision("SELL", "risk_off_regime_exit", debug)
        return StrategyDecision("HOLD", "risk_off_regime", debug)

    if trend_regime:
        underlying_decision = _multifactor_evaluate(
            recent_closes,
            in_position,
            entry_price,
            cooldown_remaining,
            bars_since_entry,
            position_context,
            config.multi_factor_config,
        )
        return StrategyDecision(
            signal=underlying_decision.signal,
            reason=f"trend_following::{underlying_decision.reason}",
            debug={**debug, "selected_strategy": "multi_factor", "underlying_decision": underlying_decision.debug},
            buy_fraction_pct_override=underlying_decision.buy_fraction_pct_override,
        )

    if mean_reversion_regime or in_position:
        underlying_decision = _mean_reversion_evaluate(
            recent_closes,
            in_position,
            entry_price,
            cooldown_remaining,
            bars_since_entry,
            position_context,
            config.mean_reversion_config,
        )
        return StrategyDecision(
            signal=underlying_decision.signal,
            reason=f"mean_reversion::{underlying_decision.reason}",
            debug={**debug, "selected_strategy": "mean_reversion", "underlying_decision": underlying_decision.debug},
            buy_fraction_pct_override=underlying_decision.buy_fraction_pct_override,
        )

    return StrategyDecision("HOLD", "neutral_regime", debug)


def build_mean_reversion_strategy() -> Strategy:
    config = MeanReversionConfig(
        bollinger_period=int(os.getenv("ROOSTOO_MR_BOLLINGER_PERIOD", "20")),
        bollinger_stddev=float(os.getenv("ROOSTOO_MR_BOLLINGER_STDDEV", "2.0")),
        trend_ema_period=int(os.getenv("ROOSTOO_MR_TREND_EMA_PERIOD", "50")),
        rsi_period=int(os.getenv("ROOSTOO_MR_RSI_PERIOD", "14")),
        rsi_entry_threshold=float(os.getenv("ROOSTOO_MR_RSI_ENTRY_THRESHOLD", "30")),
        rsi_exit_threshold=float(os.getenv("ROOSTOO_MR_RSI_EXIT_THRESHOLD", "56")),
        max_trend_distance_pct=float(os.getenv("ROOSTOO_MR_MAX_TREND_DISTANCE_PCT", "1.5")),
        stop_loss_pct=float(os.getenv("ROOSTOO_MR_STOP_LOSS_PCT", "0.01")),
        take_profit_pct=float(os.getenv("ROOSTOO_MR_TAKE_PROFIT_PCT", "0.015")),
        cooldown_periods=int(os.getenv("ROOSTOO_MR_COOLDOWN_PERIODS", "4")),
        lower_band_entry_buffer_pct=float(os.getenv("ROOSTOO_MR_LOWER_BAND_ENTRY_BUFFER_PCT", "0.003")),
        allow_scale_in=_to_bool(os.getenv("ROOSTOO_MR_ALLOW_SCALE_IN", "true"), default=True),
        min_distance_to_mid_pct=float(os.getenv("ROOSTOO_MR_MIN_DISTANCE_TO_MID_PCT", "0.004")),
        middle_band_exit_buffer_pct=float(os.getenv("ROOSTOO_MR_MIDDLE_BAND_EXIT_BUFFER_PCT", "0.002")),
        minimum_hold_bars=int(os.getenv("ROOSTOO_MR_MINIMUM_HOLD_BARS", "2")),
        weak_rsi_entry_threshold=float(os.getenv("ROOSTOO_MR_WEAK_RSI_ENTRY_THRESHOLD", "34")),
        strong_buy_fraction_pct=float(os.getenv("ROOSTOO_MR_STRONG_BUY_FRACTION_PCT", "0.10")),
        weak_buy_fraction_pct=float(os.getenv("ROOSTOO_MR_WEAK_BUY_FRACTION_PCT", "0.05")),
        assumed_round_trip_cost_pct=float(os.getenv("ROOSTOO_MR_ASSUMED_ROUND_TRIP_COST_PCT", "0.0025")),
        minimum_edge_to_cost_ratio=float(os.getenv("ROOSTOO_MR_MIN_EDGE_TO_COST_RATIO", "1.5")),
        volatility_period=int(os.getenv("ROOSTOO_MR_VOLATILITY_PERIOD", "20")),
        max_volatility=float(os.getenv("ROOSTOO_MR_MAX_VOLATILITY", "0.016")),
        trend_extension_enabled=_to_bool(os.getenv("ROOSTOO_MR_TREND_EXTENSION_ENABLED", "false"), default=False),
        trend_extension_ema_fast_period=int(os.getenv("ROOSTOO_MR_TREND_EXTENSION_EMA_FAST_PERIOD", "9")),
        trend_extension_ema_slow_period=int(os.getenv("ROOSTOO_MR_TREND_EXTENSION_EMA_SLOW_PERIOD", "21")),
        trend_extension_rsi_threshold=float(os.getenv("ROOSTOO_MR_TREND_EXTENSION_RSI_THRESHOLD", "55")),
        trend_extension_trailing_stop_pct=float(
            os.getenv("ROOSTOO_MR_TREND_EXTENSION_TRAILING_STOP_PCT", "0.012")
        ),
        require_price_above_trend_ema_for_entry=_to_bool(
            os.getenv("ROOSTOO_MR_REQUIRE_PRICE_ABOVE_TREND_EMA_FOR_ENTRY", "false"),
            default=False,
        ),
        entry_below_trend_ema_buffer_pct=float(
            os.getenv("ROOSTOO_MR_ENTRY_BELOW_TREND_EMA_BUFFER_PCT", "0.0")
        ),
    )
    return Strategy(name="mean_reversion", config=config, evaluator=_mean_reversion_evaluate)


def build_multifactor_strategy() -> Strategy:
    config = MultiFactorConfig(
        ema_fast_period=int(os.getenv("ROOSTOO_EMA_FAST_PERIOD", "9")),
        ema_slow_period=int(os.getenv("ROOSTOO_EMA_SLOW_PERIOD", "21")),
        ema_regime_period=int(os.getenv("ROOSTOO_EMA_REGIME_PERIOD", "50")),
        ema_slope_period=int(os.getenv("ROOSTOO_EMA_SLOPE_PERIOD", "20")),
        rsi_period=int(os.getenv("ROOSTOO_RSI_PERIOD", "14")),
        volatility_period=int(os.getenv("ROOSTOO_VOLATILITY_PERIOD", "20")),
        entry_score_threshold=float(os.getenv("ROOSTOO_ENTRY_SCORE_THRESHOLD", "0.80")),
        exit_score_threshold=float(os.getenv("ROOSTOO_EXIT_SCORE_THRESHOLD", "0.45")),
        stop_loss_pct=float(os.getenv("ROOSTOO_STOP_LOSS_PCT", "0.012")),
        trailing_stop_pct=float(os.getenv("ROOSTOO_TRAILING_STOP_PCT", "0.015")),
        max_volatility=float(os.getenv("ROOSTOO_MAX_VOLATILITY", "0.016")),
        cooldown_periods=int(os.getenv("ROOSTOO_COOLDOWN_PERIODS", "12")),
        pullback_tolerance_pct=float(os.getenv("ROOSTOO_PULLBACK_TOLERANCE_PCT", "0.0035")),
        regime_break_buffer_pct=float(os.getenv("ROOSTOO_REGIME_BREAK_BUFFER_PCT", "0.01")),
        use_volatility_regime_filter=_to_bool(
            os.getenv("ROOSTOO_USE_VOLATILITY_REGIME_FILTER", "true"),
            default=True,
        ),
        use_score_exit=_to_bool(os.getenv("ROOSTOO_USE_SCORE_EXIT", "false"), default=False),
        minimum_entry_rsi=float(os.getenv("ROOSTOO_MIN_TREND_ENTRY_RSI", "50")),
        minimum_entry_ema_slope=float(os.getenv("ROOSTOO_MIN_TREND_ENTRY_EMA_SLOPE", "0.0")),
        trend_weight=float(os.getenv("ROOSTOO_TREND_WEIGHT", "0.40")),
        pullback_weight=float(os.getenv("ROOSTOO_PULLBACK_WEIGHT", "0.25")),
        rsi_weight=float(os.getenv("ROOSTOO_RSI_WEIGHT", "0.20")),
        volatility_penalty_weight=float(os.getenv("ROOSTOO_VOLATILITY_PENALTY_WEIGHT", "0.15")),
    )
    return Strategy(name="multi_factor", config=config, evaluator=_multifactor_evaluate)


def build_mtf_mean_reversion_strategy() -> Strategy:
    config = MultiTimeframeMeanReversionConfig(
        base_candle_minutes=int(os.getenv("ROOSTOO_MTF_BASE_CANDLE_MINUTES", "15")),
        hourly_candle_minutes=int(os.getenv("ROOSTOO_MTF_HOURLY_CANDLE_MINUTES", "60")),
        four_hour_candle_minutes=int(os.getenv("ROOSTOO_MTF_FOUR_HOUR_CANDLE_MINUTES", "240")),
        bollinger_period=int(os.getenv("ROOSTOO_MTF_BOLLINGER_PERIOD", "20")),
        bollinger_stddev=float(os.getenv("ROOSTOO_MTF_BOLLINGER_STDDEV", "2.0")),
        trend_ema_period=int(os.getenv("ROOSTOO_MTF_TREND_EMA_PERIOD", "50")),
        rsi_period=int(os.getenv("ROOSTOO_MTF_RSI_PERIOD", "14")),
        rsi_entry_threshold=float(os.getenv("ROOSTOO_MTF_RSI_ENTRY_THRESHOLD", "30")),
        rsi_exit_threshold=float(os.getenv("ROOSTOO_MTF_RSI_EXIT_THRESHOLD", "52")),
        max_trend_distance_pct=float(os.getenv("ROOSTOO_MTF_MAX_TREND_DISTANCE_PCT", "1.5")),
        stop_loss_pct=float(os.getenv("ROOSTOO_MTF_STOP_LOSS_PCT", "0.01")),
        take_profit_pct=float(os.getenv("ROOSTOO_MTF_TAKE_PROFIT_PCT", "0.015")),
        cooldown_periods=int(os.getenv("ROOSTOO_MTF_COOLDOWN_PERIODS", "4")),
        min_distance_to_mid_pct=float(os.getenv("ROOSTOO_MTF_MIN_DISTANCE_TO_MID_PCT", "0.004")),
        assumed_round_trip_cost_pct=float(os.getenv("ROOSTOO_MTF_ASSUMED_ROUND_TRIP_COST_PCT", "0.0025")),
        minimum_edge_to_cost_ratio=float(os.getenv("ROOSTOO_MTF_MIN_EDGE_TO_COST_RATIO", "1.5")),
    )
    return Strategy(
        name="mtf_mean_reversion",
        config=config,
        evaluator=_mtf_mean_reversion_evaluate,
    )


def build_mtf_mean_reversion_v2_strategy() -> Strategy:
    config = MultiTimeframeMeanReversionV2Config(
        base_candle_minutes=int(os.getenv("ROOSTOO_MTF_V2_BASE_CANDLE_MINUTES", "5")),
        filter_candle_minutes=int(os.getenv("ROOSTOO_MTF_V2_FILTER_CANDLE_MINUTES", "15")),
        filter_bollinger_period=int(os.getenv("ROOSTOO_MTF_V2_FILTER_BOLLINGER_PERIOD", "20")),
        filter_bollinger_stddev=float(os.getenv("ROOSTOO_MTF_V2_FILTER_BOLLINGER_STDDEV", "2.0")),
        filter_trend_ema_period=int(os.getenv("ROOSTOO_MTF_V2_FILTER_TREND_EMA_PERIOD", "50")),
        filter_rsi_period=int(os.getenv("ROOSTOO_MTF_V2_FILTER_RSI_PERIOD", "14")),
        filter_volatility_period=int(os.getenv("ROOSTOO_MTF_V2_FILTER_VOLATILITY_PERIOD", "20")),
        filter_max_volatility=float(os.getenv("ROOSTOO_MTF_V2_FILTER_MAX_VOLATILITY", "0.013")),
        filter_min_distance_to_mid_pct=float(
            os.getenv("ROOSTOO_MTF_V2_FILTER_MIN_DISTANCE_TO_MID_PCT", "0.009")
        ),
        filter_entry_below_trend_ema_buffer_pct=float(
            os.getenv("ROOSTOO_MTF_V2_FILTER_ENTRY_BELOW_TREND_EMA_BUFFER_PCT", "0.005")
        ),
        exec_bollinger_period=int(os.getenv("ROOSTOO_MTF_V2_EXEC_BOLLINGER_PERIOD", "20")),
        exec_bollinger_stddev=float(os.getenv("ROOSTOO_MTF_V2_EXEC_BOLLINGER_STDDEV", "2.0")),
        exec_rsi_period=int(os.getenv("ROOSTOO_MTF_V2_EXEC_RSI_PERIOD", "14")),
        exec_rsi_entry_threshold=float(os.getenv("ROOSTOO_MTF_V2_EXEC_RSI_ENTRY_THRESHOLD", "28")),
        exec_rsi_exit_threshold=float(os.getenv("ROOSTOO_MTF_V2_EXEC_RSI_EXIT_THRESHOLD", "58")),
        exec_volatility_period=int(os.getenv("ROOSTOO_MTF_V2_EXEC_VOLATILITY_PERIOD", "20")),
        exec_max_volatility=float(os.getenv("ROOSTOO_MTF_V2_EXEC_MAX_VOLATILITY", "0.010")),
        exec_lower_band_entry_buffer_pct=float(
            os.getenv("ROOSTOO_MTF_V2_EXEC_LOWER_BAND_ENTRY_BUFFER_PCT", "0.002")
        ),
        stop_loss_pct=float(os.getenv("ROOSTOO_MTF_V2_STOP_LOSS_PCT", "0.007")),
        take_profit_pct=float(os.getenv("ROOSTOO_MTF_V2_TAKE_PROFIT_PCT", "0.011")),
        cooldown_periods=int(os.getenv("ROOSTOO_MTF_V2_COOLDOWN_PERIODS", "4")),
        minimum_hold_bars=int(os.getenv("ROOSTOO_MTF_V2_MINIMUM_HOLD_BARS", "3")),
        strong_buy_fraction_pct=float(os.getenv("ROOSTOO_MTF_V2_STRONG_BUY_FRACTION_PCT", "0.08")),
        assumed_round_trip_cost_pct=float(
            os.getenv("ROOSTOO_MTF_V2_ASSUMED_ROUND_TRIP_COST_PCT", "0.0025")
        ),
        minimum_edge_to_cost_ratio=float(os.getenv("ROOSTOO_MTF_V2_MIN_EDGE_TO_COST_RATIO", "1.5")),
        trend_extension_enabled=_to_bool(
            os.getenv("ROOSTOO_MTF_V2_TREND_EXTENSION_ENABLED", "true"),
            default=True,
        ),
        trend_extension_ema_fast_period=int(os.getenv("ROOSTOO_MTF_V2_TREND_EXTENSION_EMA_FAST_PERIOD", "9")),
        trend_extension_ema_slow_period=int(os.getenv("ROOSTOO_MTF_V2_TREND_EXTENSION_EMA_SLOW_PERIOD", "21")),
        trend_extension_rsi_threshold=float(
            os.getenv("ROOSTOO_MTF_V2_TREND_EXTENSION_RSI_THRESHOLD", "55")
        ),
        trend_extension_trailing_stop_pct=float(
            os.getenv("ROOSTOO_MTF_V2_TREND_EXTENSION_TRAILING_STOP_PCT", "0.009")
        ),
    )
    return Strategy(
        name="mtf_mean_reversion_v2",
        config=config,
        evaluator=_mtf_mean_reversion_v2_evaluate,
    )


def build_regime_switch_strategy() -> Strategy:
    config = RegimeSwitchConfig(
        regime_ema_period=int(os.getenv("ROOSTOO_REGIME_EMA_PERIOD", "50")),
        regime_slope_ema_period=int(os.getenv("ROOSTOO_REGIME_SLOPE_EMA_PERIOD", "20")),
        regime_slope_period=int(os.getenv("ROOSTOO_REGIME_SLOPE_PERIOD", "5")),
        volatility_period=int(os.getenv("ROOSTOO_REGIME_VOLATILITY_PERIOD", "20")),
        minimum_trend_slope=float(os.getenv("ROOSTOO_REGIME_MIN_TREND_SLOPE", "0.0")),
        flat_slope_threshold=float(os.getenv("ROOSTOO_REGIME_FLAT_SLOPE_THRESHOLD", "0.001")),
        trend_following_max_volatility=float(
            os.getenv("ROOSTOO_REGIME_TREND_FOLLOWING_MAX_VOLATILITY", "0.016")
        ),
        mean_reversion_min_volatility=float(
            os.getenv("ROOSTOO_REGIME_MEAN_REVERSION_MIN_VOLATILITY", "0.007")
        ),
        mean_reversion_max_volatility=float(
            os.getenv("ROOSTOO_REGIME_MEAN_REVERSION_MAX_VOLATILITY", "0.016")
        ),
        risk_off_volatility=float(os.getenv("ROOSTOO_REGIME_RISK_OFF_VOLATILITY", "0.02")),
        mean_reversion_price_distance_pct=float(
            os.getenv("ROOSTOO_REGIME_MEAN_REVERSION_PRICE_DISTANCE_PCT", "0.01")
        ),
        mean_reversion_max_trend_distance_pct=float(
            os.getenv("ROOSTOO_REGIME_MEAN_REVERSION_MAX_TREND_DISTANCE_PCT", "2.0")
        ),
        risk_off_break_buffer_pct=float(os.getenv("ROOSTOO_REGIME_RISK_OFF_BREAK_BUFFER_PCT", "0.01")),
        cooldown_periods=int(os.getenv("ROOSTOO_REGIME_COOLDOWN_PERIODS", "6")),
        mean_reversion_config=build_mean_reversion_strategy().config,
        multi_factor_config=build_multifactor_strategy().config,
    )
    return Strategy(name="regime_switch", config=config, evaluator=_regime_switch_evaluate)


STRATEGY_BUILDERS: Dict[str, Callable[[], Strategy]] = {
    "mean_reversion": build_mean_reversion_strategy,
    "multi_factor": build_multifactor_strategy,
    "mtf_mean_reversion": build_mtf_mean_reversion_strategy,
    "mtf_mean_reversion_v2": build_mtf_mean_reversion_v2_strategy,
    "regime_switch": build_regime_switch_strategy,
}


def build_strategy_from_env() -> Strategy:
    strategy_name = os.getenv("ROOSTOO_STRATEGY", "mean_reversion").strip().lower()
    builder = STRATEGY_BUILDERS.get(strategy_name)
    if builder is None:
        supported = ", ".join(sorted(STRATEGY_BUILDERS))
        raise ValueError(f"Unsupported ROOSTOO_STRATEGY '{strategy_name}'. Supported values: {supported}")
    return builder()


def evaluate_strategy(
    recent_closes: Sequence[float],
    in_position: bool,
    entry_price: Optional[float],
    cooldown_remaining: int,
    bars_since_entry: int,
    strategy: Strategy,
    position_context: Optional[Dict[str, Any]] = None,
) -> StrategyDecision:
    return strategy.evaluate(
        recent_closes=recent_closes,
        in_position=in_position,
        entry_price=entry_price,
        cooldown_remaining=cooldown_remaining,
        bars_since_entry=bars_since_entry,
        position_context=position_context,
    )
