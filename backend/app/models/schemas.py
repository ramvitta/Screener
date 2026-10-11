from datetime import date, datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints
from typing_extensions import Annotated

MAX_QTY = 100_000
MAX_PRICE = 10_000_000.0
MAX_LEGS = 8
MAX_NOTES_LEN = 2000

# Money/price numbers must be real, finite values — NaN/Inf would otherwise
# pass float parsing and silently poison P&L math.
Price = Annotated[float, Field(ge=0, le=MAX_PRICE, allow_inf_nan=False)]
PositivePrice = Annotated[float, Field(gt=0, le=MAX_PRICE, allow_inf_nan=False)]
Quantity = Annotated[int, Field(gt=0, le=MAX_QTY)]
def _normalise_symbol(v):
    return v.strip().upper() if isinstance(v, str) else v


# Normalised *before* the pattern runs (pydantic checks the pattern first).
Symbol = Annotated[str, BeforeValidator(_normalise_symbol), StringConstraints(pattern=r"^[A-Z0-9&_-]{1,20}$")]
Notes = Annotated[str, StringConstraints(max_length=MAX_NOTES_LEN)]


class StrictModel(BaseModel):
    """Request bodies reject unknown fields, so a client can't smuggle in
    server-owned fields (status, realized_pnl, ...)."""

    model_config = ConfigDict(extra="forbid")


class OptionType(str, Enum):
    CE = "CE"
    PE = "PE"


class LegAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class StrategyType(str, Enum):
    bull_put_spread = "bull_put_spread"
    bear_call_spread = "bear_call_spread"
    iron_condor = "iron_condor"
    # Logged from the Chain & Entry tab (Phase 4) — not screener-generated,
    # so these never appear in screener_rules' CHECK constraint, only in
    # position_logs (an unconstrained TEXT column) via POST /api/v1/positions.
    straddle = "straddle"
    strangle = "strangle"


class AlertType(str, Enum):
    screener_signal = "screener_signal"
    profit_target = "profit_target"
    max_loss = "max_loss"
    margin_call = "margin_call"


class PositionStatus(str, Enum):
    open = "open"
    closed = "closed"


class DataSource(str, Enum):
    live = "live"
    clickhouse = "clickhouse"
    sample = "sample"


class Leg(BaseModel):
    strike: float
    option_type: OptionType
    action: LegAction
    delta: float
    iv: float
    price: Optional[float] = None  # premium the signal was priced from


class Greeks(BaseModel):
    delta: float
    theta: float
    vega: float
    gamma: float


class RiskReward(BaseModel):
    max_profit: float
    max_loss: float
    breakeven: float  # upper breakeven for an iron condor
    pop: float
    breakeven_lower: Optional[float] = None  # iron condor only


class ScreenerSignal(BaseModel):
    signal_id: str
    strategy_type: StrategyType
    symbol: str
    expiry: date
    legs: List[Leg]
    greeks: Greeks
    risk_reward: RiskReward
    reason: str
    generated_at: datetime
    data_source: DataSource
    reason_source: str = "template"  # "template" (engine) or "llm" (validated model text)


class PositionLegCreate(StrictModel):
    """Input shape for logging a leg — no id/ltp yet; ltp is initialized to
    entry_price server-side until a live quote (or manual refresh) updates it."""

    strike: PositivePrice
    option_type: OptionType
    action: LegAction
    qty: Quantity
    entry_price: PositivePrice


class PositionLeg(BaseModel):
    id: int
    strike: float
    option_type: OptionType
    action: LegAction
    qty: int
    entry_price: float
    ltp: float
    sl: Optional[float] = None
    tsl_trigger: Optional[float] = None
    tsl_step: Optional[float] = None
    target: Optional[float] = None
    roll_to_strike: Optional[float] = None
    closed_at: Optional[datetime] = None
    exit_price: Optional[float] = None
    realized_pnl: Optional[float] = None
    unrealized_pnl: float = 0.0


class PositionLegUpdate(StrictModel):
    """SL/TSL/target/roll-to are alert thresholds and roll intentions only —
    read-only execution model: nothing here places a broker order."""

    sl: Optional[Price] = None
    tsl_trigger: Optional[Price] = None
    tsl_step: Optional[Price] = None
    target: Optional[Price] = None
    roll_to_strike: Optional[PositivePrice] = None


class LegExitRequest(StrictModel):
    exit_price: Price  # 0 is valid: an option can expire worthless


class PositionCreate(StrictModel):
    strategy_type: StrategyType
    symbol: Symbol
    expiry: date
    legs: List[PositionLegCreate] = Field(min_length=1, max_length=MAX_LEGS)
    entry_time: datetime
    profit_target: Optional[Price] = None
    max_loss: Optional[Price] = None
    notes: Optional[Notes] = None


class PositionUpdate(StrictModel):
    """Only free-text notes are client-writable. status / exit_time /
    realized_pnl are owned by the server (derived when legs are exited), so a
    caller can't overwrite booked P&L by hand."""

    notes: Optional[Notes] = None


class Position(BaseModel):
    id: int
    strategy_type: StrategyType
    symbol: str
    expiry: date
    legs: List[PositionLeg]
    entry_time: datetime
    exit_time: Optional[datetime] = None
    status: PositionStatus
    profit_target: Optional[float] = None
    max_loss: Optional[float] = None
    realized_pnl: Optional[float] = None
    unrealized_pnl: float = 0.0
    booked_pnl: float = 0.0
    notes: Optional[str] = None


class ScreenerRule(BaseModel):
    id: Optional[int] = None
    strategy_type: StrategyType
    symbol: str = "NIFTY"
    is_active: bool = True
    params: dict


class Alert(BaseModel):
    id: int
    alert_type: AlertType
    strategy_type: Optional[StrategyType] = None
    symbol: str
    payload: dict
    is_read: bool
    created_at: datetime


class IndexQuote(BaseModel):
    symbol: str
    spot: float
    change_pct: float


class BackfillStatus(BaseModel):
    contract_id: str
    symbol: str
    expiry: date
    strike: float
    option_type: OptionType
    last_completed_date: Optional[date] = None
    status: str


class PayoffLeg(BaseModel):
    """`iv` comes straight through for a screener signal (which already
    carries it). A logged position's leg has no stored iv — pass `iv=0` and
    `current_price` (its live ltp), and the endpoint solves iv from that
    live price instead, so a position's payoff reflects its current market
    read rather than its stale entry-day iv.

    `entry_price` is optional: a not-yet-logged screener signal has no real
    fill price, so omitting it tells the endpoint to use today's live
    theoretical premium (Black-Scholes at the current spot) as the assumed
    entry — i.e. "what if I entered this combo right now"."""

    strike: PositivePrice
    option_type: OptionType
    action: LegAction
    qty: Quantity
    entry_price: Optional[Price] = None
    iv: float = Field(default=0.0, ge=0, le=5.0, allow_inf_nan=False)
    current_price: Optional[Price] = None


class PayoffRequest(BaseModel):
    symbol: Symbol
    expiry: date
    legs: List[PayoffLeg] = Field(min_length=1, max_length=MAX_LEGS)
    target_date: Optional[date] = None  # defaults to today ("now") if omitted


class PayoffResponse(BaseModel):
    spot_range: List[float]
    current_spot: float
    at_expiry: List[float]
    at_target: List[float]
    breakevens: List[float]
    max_profit: float
    max_loss: float
    target_date: date


class ChainLegQuote(BaseModel):
    strike: float
    ltp: float
    day_open: float
    diff_pct: float


class ChainRow(BaseModel):
    otm: int  # 0 = ATM, positive = OTM calls / ITM puts side, by distance from ATM
    strike: float
    ce: Optional[ChainLegQuote] = None
    pe: Optional[ChainLegQuote] = None
    combined_ltp: Optional[float] = None
    combined_open: Optional[float] = None
    combined_diff_pct: Optional[float] = None


class ChainResponse(BaseModel):
    symbol: str
    expiry: date
    spot: float
    atm_strike: float
    updated_at: datetime
    rows: List[ChainRow]


class OiRow(BaseModel):
    otm: int
    strike: float
    call_oi: Optional[int] = None
    call_oi_change: Optional[int] = None
    put_oi: Optional[int] = None
    put_oi_change: Optional[int] = None


class SessionIvPoint(BaseModel):
    ts: datetime
    atm_iv: float


class OiPcrResponse(BaseModel):
    symbol: str
    expiry: date
    spot: float
    atm_strike: float
    as_of: Optional[datetime] = None
    call_oi_total: int
    put_oi_total: int
    oi_pcr: Optional[float] = None
    call_vol_total: int
    put_vol_total: int
    vol_pcr: Optional[float] = None
    rows: List[OiRow]
    session_iv: List[SessionIvPoint]


class KellySizingResponse(BaseModel):
    capital: float
    assignment_exposure: float
    exposure_pct_of_capital: float
    premium_collected: float
    managed_sl_risk: float
    managed_sl_risk_pct_of_capital: float
    over_allocated: bool
    open_leg_count: int
