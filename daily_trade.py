import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional


@dataclass
class DailyTradeRequirementConfig:
    enabled: bool = False
    trigger_hour_utc: int = 18
    trigger_minute_utc: int = 0
    fallback_pair: Optional[str] = None
    fallback_buy_fraction_pct: float = 0.01

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DailyTradeRequirementState:
    trade_date: Optional[str] = None
    executed_trades_today: int = 0
    fallback_attempted_today: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "DailyTradeRequirementState":
        return cls(
            trade_date=str(payload.get("trade_date")) if payload.get("trade_date") else None,
            executed_trades_today=max(int(payload.get("executed_trades_today", 0)), 0),
            fallback_attempted_today=bool(payload.get("fallback_attempted_today", False)),
        )


def build_daily_trade_requirement_config_from_env(default_pair: Optional[str]) -> DailyTradeRequirementConfig:
    fallback_pair = os.getenv("ROOSTOO_DAILY_TRADE_FALLBACK_PAIR", "").strip() or default_pair
    config = DailyTradeRequirementConfig(
        enabled=os.getenv("ROOSTOO_REQUIRE_DAILY_TRADE", "false").lower() == "true",
        trigger_hour_utc=int(os.getenv("ROOSTOO_DAILY_TRADE_TRIGGER_HOUR_UTC", "18")),
        trigger_minute_utc=int(os.getenv("ROOSTOO_DAILY_TRADE_TRIGGER_MINUTE_UTC", "0")),
        fallback_pair=fallback_pair,
        fallback_buy_fraction_pct=float(os.getenv("ROOSTOO_DAILY_TRADE_FALLBACK_BUY_FRACTION_PCT", "0.01")),
    )
    config.trigger_hour_utc = min(max(config.trigger_hour_utc, 0), 23)
    config.trigger_minute_utc = min(max(config.trigger_minute_utc, 0), 59)
    return config


def parse_utc_timestamp(timestamp: str) -> datetime:
    cleaned = timestamp.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    if cleaned.isdigit():
        raw_value = int(cleaned)
        if raw_value > 10**15:
            seconds_value = raw_value / 1_000_000
        elif raw_value > 10**12:
            seconds_value = raw_value / 1_000
        else:
            seconds_value = raw_value
        return datetime.fromtimestamp(seconds_value, tz=timezone.utc)

    parsed = datetime.fromisoformat(cleaned)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def roll_daily_trade_state(
    state: DailyTradeRequirementState,
    current_time_utc: datetime,
) -> DailyTradeRequirementState:
    trade_date = current_time_utc.date().isoformat()
    if state.trade_date == trade_date:
        return state
    return DailyTradeRequirementState(trade_date=trade_date)


def daily_trade_fallback_due(
    config: DailyTradeRequirementConfig,
    state: DailyTradeRequirementState,
    current_time_utc: datetime,
) -> bool:
    if not config.enabled or not config.fallback_pair:
        return False
    if state.executed_trades_today > 0 or state.fallback_attempted_today:
        return False
    trigger_tuple = (config.trigger_hour_utc, config.trigger_minute_utc)
    current_tuple = (current_time_utc.hour, current_time_utc.minute)
    return current_tuple >= trigger_tuple


def mark_daily_trade_executed(
    state: DailyTradeRequirementState,
    current_time_utc: datetime,
) -> DailyTradeRequirementState:
    rolled_state = roll_daily_trade_state(state, current_time_utc)
    rolled_state.executed_trades_today += 1
    return rolled_state


def mark_daily_trade_fallback_attempted(
    state: DailyTradeRequirementState,
    current_time_utc: datetime,
) -> DailyTradeRequirementState:
    rolled_state = roll_daily_trade_state(state, current_time_utc)
    rolled_state.fallback_attempted_today = True
    return rolled_state
