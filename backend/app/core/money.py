"""Money arithmetic. P&L is computed in Decimal (via each float's shortest
repr) and rounded to paise, so float noise like 0.1 + 0.2 never reaches a
booked number."""

from decimal import ROUND_HALF_EVEN, Decimal

_PAISE = Decimal("0.01")


def _d(x: float) -> Decimal:
    return Decimal(repr(float(x)))


def round_paise(x: Decimal) -> float:
    return float(x.quantize(_PAISE, rounding=ROUND_HALF_EVEN))


def leg_pnl(action: str, entry_price: float, price: float, qty: int) -> float:
    diff = _d(entry_price) - _d(price) if action == "SELL" else _d(price) - _d(entry_price)
    return round_paise(diff * qty)


def sum_money(values) -> float:
    return round_paise(sum((_d(v) for v in values), Decimal(0)))
