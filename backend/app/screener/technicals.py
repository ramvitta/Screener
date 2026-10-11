from typing import List, Tuple

"""Technical indicators computed off underlying spot candles."""


def rsi(closes: List[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0

    gains, losses = [], []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def ema(closes: List[float], period: int) -> float:
    if not closes:
        return 0.0
    k = 2 / (period + 1)
    value = closes[0]
    for price in closes[1:]:
        value = price * k + value * (1 - k)
    return round(value, 2)


def support_resistance(closes: List[float], lookback: int = 60) -> Tuple[float, float]:
    """Naive S/R: min/max close over the lookback window."""
    window = closes[-lookback:] if len(closes) >= lookback else closes
    if not window:
        return (0.0, 0.0)
    return (round(min(window), 2), round(max(window), 2))


def technical_bias(rsi_value: float) -> str:
    if rsi_value >= 60:
        return "mildly overbought"
    if rsi_value <= 40:
        return "mildly oversold"
    return "neutral, range-bound"
