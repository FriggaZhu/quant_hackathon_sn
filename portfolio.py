import os
from dataclasses import asdict, dataclass, field
from typing import Dict, Optional, Tuple


POSITION_EPSILON = 1e-12


@dataclass
class PortfolioConfig:
    max_allocation_pct: float = 0.20
    buy_fraction_pct: float = 0.10
    sell_fraction_pct: float = 0.75
    min_trade_notional: float = 0.0
    dust_trade_notional: float = 50.0
    use_volatility_scaling: bool = True
    volatility_period: int = 20
    target_volatility: float = 0.012
    min_buy_fraction_pct_multiplier: float = 0.50
    max_buy_fraction_pct_multiplier: float = 1.25
    use_pair_ranking: bool = True
    pair_ranking_lookback: int = 48
    min_pair_rank_multiplier: float = 0.85
    max_pair_rank_multiplier: float = 1.15
    max_ranked_buys_per_bar: int = 0

    def to_dict(self) -> Dict[str, float]:
        return asdict(self)


@dataclass
class PortfolioState:
    cash_balance: float
    position_units: float = 0.0
    average_entry_price: Optional[float] = None
    realized_pnl: float = 0.0
    fees_paid: float = 0.0

    def to_dict(self) -> Dict[str, Optional[float]]:
        return asdict(self)


@dataclass
class TradeExecution:
    side: str
    quantity: float
    notional: float
    fee_paid: float
    average_entry_price: Optional[float]
    realized_pnl: float = 0.0
    return_pct: float = 0.0
    position_fully_closed: bool = False


@dataclass
class PairPositionState:
    position_units: float = 0.0
    average_entry_price: Optional[float] = None

    def to_dict(self) -> Dict[str, Optional[float]]:
        return asdict(self)


@dataclass
class SharedPortfolioState:
    cash_balance: float
    positions: Dict[str, PairPositionState] = field(default_factory=dict)
    realized_pnl: float = 0.0
    fees_paid: float = 0.0

    def to_dict(self) -> Dict[str, object]:
        return {
            "cash_balance": self.cash_balance,
            "positions": {pair: position.to_dict() for pair, position in self.positions.items()},
            "realized_pnl": self.realized_pnl,
            "fees_paid": self.fees_paid,
        }


def build_portfolio_config_from_env() -> PortfolioConfig:
    return PortfolioConfig(
        max_allocation_pct=float(os.getenv("ROOSTOO_PORTFOLIO_MAX_ALLOCATION_PCT", "0.20")),
        buy_fraction_pct=float(os.getenv("ROOSTOO_PORTFOLIO_BUY_FRACTION_PCT", "0.10")),
        sell_fraction_pct=float(os.getenv("ROOSTOO_PORTFOLIO_SELL_FRACTION_PCT", "0.75")),
        min_trade_notional=float(os.getenv("ROOSTOO_PORTFOLIO_MIN_TRADE_NOTIONAL", "0")),
        dust_trade_notional=float(os.getenv("ROOSTOO_PORTFOLIO_DUST_TRADE_NOTIONAL", "50")),
        use_volatility_scaling=os.getenv("ROOSTOO_PORTFOLIO_USE_VOLATILITY_SCALING", "true").lower()
        in {"1", "true", "yes", "y", "on"},
        volatility_period=int(os.getenv("ROOSTOO_PORTFOLIO_VOLATILITY_PERIOD", "20")),
        target_volatility=float(os.getenv("ROOSTOO_PORTFOLIO_TARGET_VOLATILITY", "0.012")),
        min_buy_fraction_pct_multiplier=float(
            os.getenv("ROOSTOO_PORTFOLIO_MIN_BUY_FRACTION_MULTIPLIER", "0.50")
        ),
        max_buy_fraction_pct_multiplier=float(
            os.getenv("ROOSTOO_PORTFOLIO_MAX_BUY_FRACTION_MULTIPLIER", "1.25")
        ),
        use_pair_ranking=os.getenv("ROOSTOO_PORTFOLIO_USE_PAIR_RANKING", "true").lower()
        in {"1", "true", "yes", "y", "on"},
        pair_ranking_lookback=int(os.getenv("ROOSTOO_PORTFOLIO_PAIR_RANKING_LOOKBACK", "48")),
        min_pair_rank_multiplier=float(os.getenv("ROOSTOO_PORTFOLIO_MIN_PAIR_RANK_MULTIPLIER", "0.85")),
        max_pair_rank_multiplier=float(os.getenv("ROOSTOO_PORTFOLIO_MAX_PAIR_RANK_MULTIPLIER", "1.15")),
        max_ranked_buys_per_bar=int(os.getenv("ROOSTOO_PORTFOLIO_MAX_RANKED_BUYS_PER_BAR", "0")),
    )


def parse_pair_assets(pair: str) -> Tuple[str, str]:
    if "/" in pair:
        base_asset, quote_asset = pair.split("/", 1)
        return base_asset.strip().upper(), quote_asset.strip().upper()

    normalized = pair.strip().upper()
    for suffix in ("USDT", "USDC", "BUSD", "USD", "BTC", "ETH"):
        if normalized.endswith(suffix) and len(normalized) > len(suffix):
            return normalized[: -len(suffix)], suffix

    raise ValueError(f"Could not infer base and quote assets from pair: {pair}")


def total_equity(state: PortfolioState, current_price: float) -> float:
    return state.cash_balance + (state.position_units * current_price)


def position_value(state: PortfolioState, current_price: float) -> float:
    return state.position_units * current_price


def allocation_pct(state: PortfolioState, current_price: float) -> float:
    equity = total_equity(state, current_price)
    if equity <= 0:
        return 0.0
    return position_value(state, current_price) / equity


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _realized_volatility(prices: list[float], period: int) -> Optional[float]:
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
    return variance ** 0.5


def compute_buy_fraction_multiplier(
    recent_closes: list[float],
    config: PortfolioConfig,
) -> float:
    if not config.use_volatility_scaling:
        return 1.0

    volatility = _realized_volatility(recent_closes, config.volatility_period)
    if volatility is None or volatility <= 0:
        return 1.0

    raw_multiplier = config.target_volatility / volatility
    return _clamp(
        raw_multiplier,
        config.min_buy_fraction_pct_multiplier,
        config.max_buy_fraction_pct_multiplier,
    )


def _extract_active_strategy_debug(strategy_debug: Dict[str, object]) -> tuple[str, Dict[str, object]]:
    selected_strategy = strategy_debug.get("selected_strategy")
    underlying_decision = strategy_debug.get("underlying_decision")
    if isinstance(selected_strategy, str) and isinstance(underlying_decision, dict):
        return selected_strategy, underlying_decision

    strategy_name = strategy_debug.get("strategy_name")
    if isinstance(strategy_name, str):
        return strategy_name, strategy_debug

    return "unknown", strategy_debug


def _relative_return(prices: list[float], lookback: int) -> float:
    if len(prices) < 2:
        return 0.0

    effective_lookback = min(max(1, lookback), len(prices) - 1)
    previous_price = prices[-(effective_lookback + 1)]
    current_price = prices[-1]
    if previous_price <= 0:
        return 0.0

    return (current_price - previous_price) / previous_price


def compute_pair_opportunity_score(
    recent_closes: list[float],
    strategy_debug: Dict[str, object],
    config: PortfolioConfig,
) -> float:
    strategy_name, active_debug = _extract_active_strategy_debug(strategy_debug)
    momentum = _relative_return(recent_closes, config.pair_ranking_lookback)
    volatility = _realized_volatility(recent_closes, min(config.volatility_period, max(2, len(recent_closes) - 1)))
    risk_adjusted_momentum = momentum / max(volatility or 0.0, 1e-6)

    if strategy_name == "multi_factor":
        composite_score = float(active_debug.get("score", 0.0) or 0.0)
        return (composite_score * 2.0) + max(risk_adjusted_momentum, 0.0)

    if strategy_name in {"mean_reversion", "mtf_mean_reversion"}:
        distance_to_mid_pct = float(active_debug.get("distance_to_mid_pct", 0.0) or 0.0)
        required_distance_to_mid_pct = float(active_debug.get("required_distance_to_mid_pct", 0.0) or 0.0)
        rsi_value = float(active_debug.get("rsi", 50.0) or 50.0)
        volatility_value = float(active_debug.get("volatility", 0.0) or 0.0)
        max_volatility = float(active_debug.get("max_volatility", 0.0) or 0.0)
        trend_distance_pct = float(active_debug.get("trend_distance_pct", 0.0) or 0.0)
        max_trend_distance_pct = float(active_debug.get("max_trend_distance_pct", 0.0) or 0.0)
        current_price = float(active_debug.get("current_price", 0.0) or 0.0)
        lower_band = float(active_debug.get("lower_band", 0.0) or 0.0)
        bounced_from_lower_band = bool(active_debug.get("bounced_from_lower_band"))
        near_lower_band = bool(active_debug.get("near_lower_band"))

        edge_surplus_pct = max(0.0, distance_to_mid_pct - required_distance_to_mid_pct)
        edge_score = edge_surplus_pct * 250.0

        oversold_anchor = 35.0 if strategy_name == "mean_reversion" else 40.0
        oversold_score = max(0.0, (oversold_anchor - rsi_value) / 4.0)

        lower_band_gap_pct = 0.0
        if current_price > 0 and lower_band > 0:
            lower_band_gap_pct = max(0.0, (lower_band - current_price) / current_price)
        band_score = (lower_band_gap_pct * 200.0) + (1.0 if near_lower_band else 0.0) + (
            0.75 if bounced_from_lower_band else 0.0
        )

        volatility_penalty = 0.0
        if volatility_value > 0 and max_volatility > 0:
            volatility_penalty = max(0.0, volatility_value / max_volatility)

        trend_penalty = 0.0
        if trend_distance_pct > 0:
            if max_trend_distance_pct > 0:
                trend_penalty = trend_distance_pct / max_trend_distance_pct
            else:
                trend_penalty = trend_distance_pct / 2.0

        return edge_score + oversold_score + band_score - volatility_penalty - trend_penalty

    return max(risk_adjusted_momentum, 0.0)


def compute_pair_ranking_results(
    candidates: Dict[str, tuple[list[float], Dict[str, object]]],
    config: PortfolioConfig,
) -> Dict[str, Dict[str, float]]:
    results: Dict[str, Dict[str, float]] = {}
    if not candidates:
        return results

    for pair, (recent_closes, strategy_debug) in candidates.items():
        results[pair] = {
            "score": compute_pair_opportunity_score(recent_closes, strategy_debug, config),
            "multiplier": 1.0,
        }

    if not config.use_pair_ranking or len(results) <= 1:
        return results

    scores = [result["score"] for result in results.values()]
    if max(scores) - min(scores) < 1e-9:
        return results

    ordered_pairs = sorted(
        results.items(),
        key=lambda item: (-item[1]["score"], item[0]),
    )
    spread = config.max_pair_rank_multiplier - config.min_pair_rank_multiplier
    denominator = max(1, len(ordered_pairs) - 1)

    for index, (pair, result) in enumerate(ordered_pairs):
        rank_fraction = index / denominator
        result["multiplier"] = config.max_pair_rank_multiplier - (spread * rank_fraction)
        results[pair] = result

    return results


def compute_buy_notional(
    state: PortfolioState,
    current_price: float,
    config: PortfolioConfig,
    buy_fraction_pct_override: Optional[float] = None,
    buy_fraction_pct_multiplier: Optional[float] = None,
) -> float:
    equity = total_equity(state, current_price)
    if equity <= 0 or state.cash_balance <= 0:
        return 0.0

    current_position_value = position_value(state, current_price)
    max_position_value = equity * config.max_allocation_pct
    remaining_capacity = max(0.0, max_position_value - current_position_value)
    buy_fraction_pct = (
        buy_fraction_pct_override
        if buy_fraction_pct_override is not None
        else config.buy_fraction_pct
    )
    if buy_fraction_pct_multiplier is not None and buy_fraction_pct_multiplier > 0:
        buy_fraction_pct *= buy_fraction_pct_multiplier
    desired_notional = equity * buy_fraction_pct
    buy_notional = min(state.cash_balance, remaining_capacity, desired_notional)

    if buy_notional < config.min_trade_notional:
        return 0.0
    return buy_notional


def compute_sell_units(
    state: PortfolioState,
    current_price: float,
    config: PortfolioConfig,
) -> float:
    if state.position_units <= POSITION_EPSILON:
        return 0.0

    units_to_sell = state.position_units * config.sell_fraction_pct
    units_to_sell = min(units_to_sell, state.position_units)
    if units_to_sell <= POSITION_EPSILON:
        return 0.0

    sell_notional = units_to_sell * current_price
    remaining_notional = (state.position_units - units_to_sell) * current_price

    if config.min_trade_notional > 0 and sell_notional < config.min_trade_notional:
        units_to_sell = state.position_units
        remaining_notional = 0.0

    if 0 < remaining_notional < config.dust_trade_notional:
        units_to_sell = state.position_units

    return min(units_to_sell, state.position_units)


def apply_buy(
    state: PortfolioState,
    current_price: float,
    fee_rate: float,
    buy_notional: float,
) -> Optional[TradeExecution]:
    if buy_notional <= 0 or current_price <= 0:
        return None

    spend = min(buy_notional, state.cash_balance)
    if spend <= 0:
        return None

    fee_paid = spend * fee_rate
    acquired_units = (spend - fee_paid) / current_price
    if acquired_units <= POSITION_EPSILON:
        return None

    previous_units = state.position_units
    previous_cost_basis = (state.average_entry_price or 0.0) * previous_units
    new_total_units = previous_units + acquired_units
    new_cost_basis = previous_cost_basis + spend

    state.cash_balance -= spend
    state.position_units = new_total_units
    state.average_entry_price = new_cost_basis / new_total_units
    state.fees_paid += fee_paid

    return TradeExecution(
        side="BUY",
        quantity=acquired_units,
        notional=spend,
        fee_paid=fee_paid,
        average_entry_price=state.average_entry_price,
    )


def apply_sell(
    state: PortfolioState,
    current_price: float,
    fee_rate: float,
    units_to_sell: float,
) -> Optional[TradeExecution]:
    if units_to_sell <= POSITION_EPSILON or current_price <= 0:
        return None

    units_to_sell = min(units_to_sell, state.position_units)
    if units_to_sell <= POSITION_EPSILON:
        return None

    gross_notional = units_to_sell * current_price
    fee_paid = gross_notional * fee_rate
    net_notional = gross_notional - fee_paid
    average_entry_price = state.average_entry_price
    cost_basis = (average_entry_price or 0.0) * units_to_sell
    realized_pnl = net_notional - cost_basis
    return_pct = 0.0
    if cost_basis > 0:
        return_pct = (realized_pnl / cost_basis) * 100

    remaining_units = state.position_units - units_to_sell
    if remaining_units <= POSITION_EPSILON:
        state.position_units = 0.0
        state.average_entry_price = None
    else:
        state.position_units = remaining_units

    state.cash_balance += net_notional
    state.realized_pnl += realized_pnl
    state.fees_paid += fee_paid

    return TradeExecution(
        side="SELL",
        quantity=units_to_sell,
        notional=net_notional,
        fee_paid=fee_paid,
        average_entry_price=average_entry_price,
        realized_pnl=realized_pnl,
        return_pct=return_pct,
        position_fully_closed=state.position_units <= POSITION_EPSILON,
    )


def get_pair_position_state(
    portfolio_state: SharedPortfolioState,
    pair: str,
) -> PairPositionState:
    position = portfolio_state.positions.get(pair)
    if position is None:
        position = PairPositionState()
        portfolio_state.positions[pair] = position
    return position


def shared_total_equity(
    portfolio_state: SharedPortfolioState,
    latest_prices: Dict[str, float],
) -> float:
    equity = portfolio_state.cash_balance
    for pair, position in portfolio_state.positions.items():
        price = latest_prices.get(pair)
        if price is None:
            continue
        equity += position.position_units * price
    return equity


def shared_position_value(
    portfolio_state: SharedPortfolioState,
    pair: str,
    current_price: float,
) -> float:
    position = get_pair_position_state(portfolio_state, pair)
    return position.position_units * current_price


def shared_allocation_pct(
    portfolio_state: SharedPortfolioState,
    pair: str,
    latest_prices: Dict[str, float],
) -> float:
    price = latest_prices.get(pair)
    if price is None:
        return 0.0
    equity = shared_total_equity(portfolio_state, latest_prices)
    if equity <= 0:
        return 0.0
    return shared_position_value(portfolio_state, pair, price) / equity


def shared_compute_buy_notional(
    portfolio_state: SharedPortfolioState,
    pair: str,
    current_price: float,
    latest_prices: Dict[str, float],
    config: PortfolioConfig,
    buy_fraction_pct_override: Optional[float] = None,
    buy_fraction_pct_multiplier: Optional[float] = None,
) -> float:
    prices = dict(latest_prices)
    prices[pair] = current_price
    equity = shared_total_equity(portfolio_state, prices)
    if equity <= 0 or portfolio_state.cash_balance <= 0:
        return 0.0

    current_position_value = shared_position_value(portfolio_state, pair, current_price)
    max_position_value = equity * config.max_allocation_pct
    remaining_capacity = max(0.0, max_position_value - current_position_value)
    buy_fraction_pct = (
        buy_fraction_pct_override
        if buy_fraction_pct_override is not None
        else config.buy_fraction_pct
    )
    if buy_fraction_pct_multiplier is not None and buy_fraction_pct_multiplier > 0:
        buy_fraction_pct *= buy_fraction_pct_multiplier
    desired_notional = equity * buy_fraction_pct
    buy_notional = min(portfolio_state.cash_balance, remaining_capacity, desired_notional)
    if buy_notional < config.min_trade_notional:
        return 0.0
    return buy_notional


def shared_compute_sell_units(
    portfolio_state: SharedPortfolioState,
    pair: str,
    current_price: float,
    config: PortfolioConfig,
) -> float:
    position = get_pair_position_state(portfolio_state, pair)
    if position.position_units <= POSITION_EPSILON:
        return 0.0

    units_to_sell = position.position_units * config.sell_fraction_pct
    units_to_sell = min(units_to_sell, position.position_units)
    if units_to_sell <= POSITION_EPSILON:
        return 0.0

    sell_notional = units_to_sell * current_price
    remaining_notional = (position.position_units - units_to_sell) * current_price

    if config.min_trade_notional > 0 and sell_notional < config.min_trade_notional:
        units_to_sell = position.position_units
        remaining_notional = 0.0

    if 0 < remaining_notional < config.dust_trade_notional:
        units_to_sell = position.position_units

    return min(units_to_sell, position.position_units)


def shared_apply_buy(
    portfolio_state: SharedPortfolioState,
    pair: str,
    current_price: float,
    fee_rate: float,
    buy_notional: float,
) -> Optional[TradeExecution]:
    if buy_notional <= 0 or current_price <= 0:
        return None

    spend = min(buy_notional, portfolio_state.cash_balance)
    if spend <= 0:
        return None

    fee_paid = spend * fee_rate
    acquired_units = (spend - fee_paid) / current_price
    if acquired_units <= POSITION_EPSILON:
        return None

    position = get_pair_position_state(portfolio_state, pair)
    previous_units = position.position_units
    previous_cost_basis = (position.average_entry_price or 0.0) * previous_units
    new_total_units = previous_units + acquired_units
    new_cost_basis = previous_cost_basis + spend

    portfolio_state.cash_balance -= spend
    position.position_units = new_total_units
    position.average_entry_price = new_cost_basis / new_total_units
    portfolio_state.fees_paid += fee_paid

    return TradeExecution(
        side="BUY",
        quantity=acquired_units,
        notional=spend,
        fee_paid=fee_paid,
        average_entry_price=position.average_entry_price,
    )


def shared_apply_sell(
    portfolio_state: SharedPortfolioState,
    pair: str,
    current_price: float,
    fee_rate: float,
    units_to_sell: float,
) -> Optional[TradeExecution]:
    position = get_pair_position_state(portfolio_state, pair)
    if units_to_sell <= POSITION_EPSILON or current_price <= 0:
        return None

    units_to_sell = min(units_to_sell, position.position_units)
    if units_to_sell <= POSITION_EPSILON:
        return None

    gross_notional = units_to_sell * current_price
    fee_paid = gross_notional * fee_rate
    net_notional = gross_notional - fee_paid
    average_entry_price = position.average_entry_price
    cost_basis = (average_entry_price or 0.0) * units_to_sell
    realized_pnl = net_notional - cost_basis
    return_pct = 0.0
    if cost_basis > 0:
        return_pct = (realized_pnl / cost_basis) * 100

    remaining_units = position.position_units - units_to_sell
    if remaining_units <= POSITION_EPSILON:
        position.position_units = 0.0
        position.average_entry_price = None
    else:
        position.position_units = remaining_units

    portfolio_state.cash_balance += net_notional
    portfolio_state.realized_pnl += realized_pnl
    portfolio_state.fees_paid += fee_paid

    return TradeExecution(
        side="SELL",
        quantity=units_to_sell,
        notional=net_notional,
        fee_paid=fee_paid,
        average_entry_price=average_entry_price,
        realized_pnl=realized_pnl,
        return_pct=return_pct,
        position_fully_closed=position.position_units <= POSITION_EPSILON,
    )
