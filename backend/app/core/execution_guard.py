"""Single choke point for any future order placement.

The app is a read-only screener/logger: DefinedgeClient has no order methods
and tests/security/test_no_order_paths.py fails if one appears elsewhere. If
order placement is ever added, it MUST call `authorize_order()` first. The
guard refuses unless trading is explicitly enabled AND every limit below is
satisfied, so a bug or a prompt-injected model cannot place an order by
accident.
"""

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from app.config import settings

# Presence of this file is a kill switch that overrides everything else.
KILL_SWITCH_FILE = Path(__file__).resolve().parents[2] / "KILL_SWITCH"


class OrderRefused(RuntimeError):
    pass


@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    qty: int
    price: float
    dry_run: bool = True  # dry-run by default; callers must opt out explicitly
    confirmation_token: Optional[str] = None


@dataclass(frozen=True)
class OrderLimits:
    max_order_value: float
    max_daily_loss: float
    realized_loss_today: float
    symbol_allowlist: Iterable[str]
    expected_confirmation: str


def authorize_order(order: OrderRequest, limits: OrderLimits) -> None:
    if KILL_SWITCH_FILE.exists():
        raise OrderRefused("Kill switch engaged")
    if settings.trading_mode != "live":
        raise OrderRefused(f"TRADING_MODE is '{settings.trading_mode}' — order placement is disabled")
    if order.dry_run:
        raise OrderRefused("Dry-run order — nothing is sent to the broker")
    if not (order.qty > 0 and math.isfinite(order.price) and order.price > 0):
        raise OrderRefused("Invalid quantity or price")
    if order.symbol not in set(limits.symbol_allowlist):
        raise OrderRefused(f"Symbol {order.symbol!r} is not on the allowlist")
    if order.qty * order.price > limits.max_order_value:
        raise OrderRefused("Order value exceeds the per-order limit")
    if limits.realized_loss_today >= limits.max_daily_loss:
        raise OrderRefused("Daily loss limit reached")
    if not order.confirmation_token or order.confirmation_token != limits.expected_confirmation:
        raise OrderRefused("Missing or invalid human confirmation token")
