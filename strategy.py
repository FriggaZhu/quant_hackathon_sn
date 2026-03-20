from typing import Sequence


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
