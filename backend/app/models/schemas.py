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
    # Straddle/strangle are loggable positions (Phase 4) AND, since the
    # str-stra port (workers/str_stra_loop.py), screener-generated signals.
    # They never appear in screener_rules' CHECK constraint (that table only
    # holds the three credit-spread rules), only in position_logs/screener_signals.
    straddle = "straddle"
    strangle = "strangle"
    # str-stra strategy families (screener-only — never a position_logs type).
    adx_direction = "adx_direction"
    unified = "unified"


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
    openalgo = "openalgo"  # legacy: former str-stra bridge (now sourced from Definedge as "live")


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


# --- Admin historical-data-download (proactive archival — see
# integrations/history_downloader.py for why expired contracts are out of
# scope: the broker's history endpoint stops serving a contract once it
# rolls off the live instrument master) ---


class FnoStock(BaseModel):
    symbol: str
    lot_size: int
    nearest_expiry: Optional[date] = None


class DownloadScope(StrictModel):
    equity: bool = True
    futures: bool = True
    options: bool = True
    strike_band: int = Field(default=10, ge=1, le=40)  # nearest N strikes per side, per expiry


class DownloadValidateRequest(StrictModel):
    symbol: Symbol
    start_date: date
    end_date: date
    scope: DownloadScope = DownloadScope()


class DownloadValidateItem(BaseModel):
    instrument_key: str
    label: str
    available: bool
    note: Optional[str] = None


class DownloadValidateResponse(BaseModel):
    symbol: str
    clamped_start_date: date  # equity history is capped ~6mo back; clamped silently, shown here
    clamped_end_date: date
    items: List[DownloadValidateItem]
    estimated_candle_calls: int
    warnings: List[str] = Field(default_factory=list)


class DownloadJobCreate(DownloadValidateRequest):
    pass


class DownloadJobItemStatus(BaseModel):
    instrument_key: str
    status: str
    rows: int
    error: Optional[str] = None


class DownloadJobStatus(BaseModel):
    id: int
    symbol: str
    start_date: date
    end_date: date
    status: str
    total_items: int
    completed_items: int
    total_rows: int
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class DownloadCoverageRow(BaseModel):
    instrument_type: str
    count: int
    min_ts: Optional[datetime] = None
    max_ts: Optional[datetime] = None


# --- Signal history (screener signal + reject tracking) ---


class ScreenerRun(BaseModel):
    run_id: str
    started_at: datetime
    data_source: Optional[DataSource] = None
    status: str  # ok | sample | degraded
    message: str = ""
    signal_count: int
    created_at: datetime


class SignalHistoryItem(BaseModel):
    id: int
    run_id: str
    signal_id: Optional[str] = None
    strategy_type: Optional[StrategyType] = None
    symbol: Optional[str] = None
    expiry: Optional[date] = None
    status: str  # accepted | rejected
    reject_reason: Optional[str] = None
    # Unified-screener tier (PRIME / READY / WATCH) pulled out of
    # decision_json.computed so the history list can filter/colour-code
    # without fetching every signal's full detail payload.
    tier: Optional[str] = None
    generated_at: Optional[datetime] = None
    data_source: Optional[str] = None
    reason: Optional[str] = None
    reason_source: Optional[str] = None
    created_at: datetime


class SignalHistoryDetail(SignalHistoryItem):
    signal_json: Optional[dict] = None
    decision_json: Optional[dict] = None


class ScreenerRunDetail(ScreenerRun):
    signals: List[SignalHistoryDetail] = Field(default_factory=list)


class PruneSignalsRequest(StrictModel):
    confirm: bool
    before: Optional[date] = None  # default: retention window boundary


class PruneSignalsResponse(BaseModel):
    deleted_signals: int
    deleted_runs: int
    cutoff: date


# --- Morning daily byte (Telegram via Telethon) ---


class ByteSymbolCall(BaseModel):
    ticker: str
    direction: Optional[str] = None  # long | short | neutral
    target: Optional[float] = None
    stop_loss: Optional[float] = None
    horizon: Optional[str] = None


class ByteEvent(BaseModel):
    name: str
    date: Optional[str] = None
    impact: Optional[str] = None  # high | medium | low


class ByteGlobalCue(BaseModel):
    instrument: str
    direction: Optional[str] = None  # up | down | flat


class DailyByteAnalysis(BaseModel):
    """Validated LLM extraction from one daily-byte message. Every field is
    Optional / defaulted so a partial extraction still parses; the model is
    never trusted to fill anything it didn't see in the text."""

    sentiment: Optional[str] = None  # bullish | bearish | neutral | risk-on | risk-off | unknown
    confidence: Optional[float] = None
    nifty_level: Optional[float] = None
    nifty_bias: Optional[str] = None
    nifty_support: List[float] = Field(default_factory=list)
    nifty_resistance: List[float] = Field(default_factory=list)
    nifty_targets: List[float] = Field(default_factory=list)
    banknifty_level: Optional[float] = None
    banknifty_bias: Optional[str] = None
    banknifty_support: List[float] = Field(default_factory=list)
    banknifty_resistance: List[float] = Field(default_factory=list)
    symbols: List[ByteSymbolCall] = Field(default_factory=list)
    sectors: List[str] = Field(default_factory=list)
    events: List[ByteEvent] = Field(default_factory=list)
    global_cues: List[ByteGlobalCue] = Field(default_factory=list)
    strategy_type: Optional[str] = None
    advance_decline: Optional[str] = None
    fii_dii: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)


class DailyByteSource(BaseModel):
    group_slot: int
    chat_username: Optional[str] = None
    chat_title: Optional[str] = None
    posted_at: Optional[datetime] = None
    text: str
    summary: Optional[str] = None
    analysis: Optional[DailyByteAnalysis] = None
    is_valid: bool = False


class DailyByteDigestResponse(BaseModel):
    date: str
    market_bias: Optional[str] = None
    summary: Optional[str] = None
    top_symbols: List[ByteSymbolCall] = Field(default_factory=list)
    sources: List[DailyByteSource] = Field(default_factory=list)


# --- Market breadth ---


class BreadthSnapshot(BaseModel):
    bucket: str  # hour | day | week | month
    ts: str
    metrics: dict


class BreadthResponse(BaseModel):
    universe: str
    total: int
    as_of: Optional[datetime] = None
    intraday: List[BreadthSnapshot] = Field(default_factory=list)
    daily: List[BreadthSnapshot] = Field(default_factory=list)
    weekly: List[BreadthSnapshot] = Field(default_factory=list)
    monthly: List[BreadthSnapshot] = Field(default_factory=list)


# --- Telegram trade-call tracker ---

TelegramMessageCategory = str  # trade_alert | trade_update | trade_closure | result_calendar | market_commentary | promo | other
TelegramCallStatus = str  # open | target_hit | sl_hit | closed | expired
TelegramEventType = str  # alert | target_hit | sl_hit | closed | partial_exit | trail | update


class TelegramCallExtract(BaseModel):
    """Untrusted structured extraction of one call. Every field is optional so
    a partial regex/LLM extraction still parses; numbers are checked against
    the source text before being trusted."""

    model_config = ConfigDict(extra="ignore")

    symbol: Optional[str] = None
    symbol_raw: Optional[str] = None
    display_name: Optional[str] = None
    segment: str = "other"
    exchange: Optional[str] = None
    direction: str = "neutral"
    action: str = "BUY"
    option_type: Optional[str] = None
    strike: Optional[float] = None
    expiry: Optional[str] = None
    expiry_text: Optional[str] = None
    entry_low: Optional[float] = None
    entry_high: Optional[float] = None
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    target_1: Optional[float] = None
    target_2: Optional[float] = None
    target_3: Optional[float] = None
    cmp: Optional[float] = None
    horizon: Optional[str] = None


class TelegramEventExtract(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event_type: str = "update"
    symbol: Optional[str] = None
    option_type: Optional[str] = None
    strike: Optional[float] = None
    expiry: Optional[str] = None
    price: Optional[float] = None
    target_value: Optional[float] = None
    realized_pct: Optional[float] = None
    note: Optional[str] = None


class TelegramMessageExtract(BaseModel):
    model_config = ConfigDict(extra="ignore")

    category: str = "other"
    confidence: Optional[float] = None
    summary: Optional[str] = None
    is_trade_call: bool = False
    calls: List[TelegramCallExtract] = Field(default_factory=list)
    events: List[TelegramEventExtract] = Field(default_factory=list)


class TelegramCallEvent(BaseModel):
    id: int
    call_id: Optional[int] = None
    message_fk: int
    chat_id: int
    message_id: int
    posted_at: datetime
    event_type: str
    symbol: Optional[str] = None
    option_type: Optional[str] = None
    strike: Optional[float] = None
    expiry: Optional[str] = None
    price: Optional[float] = None
    target_value: Optional[float] = None
    realized_pct: Optional[float] = None
    note: Optional[str] = None
    method: str
    is_valid: bool = True
    text: Optional[str] = None


class TelegramCall(BaseModel):
    id: int
    message_fk: int
    chat_id: int
    chat_username: Optional[str] = None
    chat_title: Optional[str] = None
    message_id: int
    posted_at: datetime
    symbol: str
    symbol_raw: Optional[str] = None
    display_name: Optional[str] = None
    segment: str = "other"
    exchange: Optional[str] = None
    direction: str
    action: str
    option_type: Optional[str] = None
    strike: Optional[float] = None
    expiry: Optional[date] = None
    expiry_text: Optional[str] = None
    entry_low: Optional[float] = None
    entry_high: Optional[float] = None
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    target_1: Optional[float] = None
    target_2: Optional[float] = None
    target_3: Optional[float] = None
    cmp: Optional[float] = None
    horizon: Optional[str] = None
    status: str
    status_reason: Optional[str] = None
    status_source: Optional[str] = None
    exit_price: Optional[float] = None
    closed_at: Optional[datetime] = None
    realized_pct: Optional[float] = None
    r_multiple: Optional[float] = None
    confidence: Optional[float] = None
    method: str
    is_valid: bool = True
    tracking_status: Optional[str] = None
    tracking_message: Optional[str] = None
    last_checked_at: Optional[datetime] = None
    last_price: Optional[float] = None
    notes: Optional[str] = None
    source_text: Optional[str] = None
    source_url: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class TelegramCallDetail(TelegramCall):
    extraction: Optional[dict] = None
    events: List[TelegramCallEvent] = Field(default_factory=list)
    thread: List["TelegramArchiveMessage"] = Field(default_factory=list)


class TelegramArchiveMessage(BaseModel):
    id: int
    chat_id: int
    chat_username: Optional[str] = None
    chat_title: Optional[str] = None
    message_id: int
    posted_at: datetime
    posted_local: Optional[datetime] = None
    sender_name: Optional[str] = None
    sender_username: Optional[str] = None
    text: str
    media_type: Optional[str] = None
    views: Optional[int] = None
    forwards: Optional[int] = None
    reply_to_msg_id: Optional[int] = None
    category: Optional[str] = None
    method: Optional[str] = None
    confidence: Optional[float] = None
    call_count: int = 0


class TelegramChatSummary(BaseModel):
    chat_id: int
    username: Optional[str] = None
    title: Optional[str] = None
    message_count: int
    first_message_at: Optional[datetime] = None
    last_message_at: Optional[datetime] = None
    call_count: int = 0
    open_count: int = 0
    target_hit_count: int = 0
    sl_hit_count: int = 0
    closed_count: int = 0


class TelegramCallStats(BaseModel):
    total_calls: int = 0
    open: int = 0
    target_hit: int = 0
    sl_hit: int = 0
    closed: int = 0
    expired: int = 0
    win_rate: Optional[float] = None
    avg_realized_pct: Optional[float] = None
    tracked: int = 0
    unresolved: int = 0
    total_messages: int = 0
    classified_messages: int = 0
    last_sync_at: Optional[datetime] = None


class TelegramSyncStatus(BaseModel):
    state: str  # idle | running | ok | degraded | error
    message: str = ""
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    since: Optional[date] = None
    max_per_chat: int = 0
    messages_stored: int = 0
    calls_found: int = 0
    chats_done: int = 0
    chats_total: int = 0
    error: Optional[str] = None


class TelegramSyncRequest(StrictModel):
    since: Optional[date] = None
    max_per_chat: Optional[int] = Field(default=None, ge=0, le=500_000)
    full: bool = False
    classify: bool = True
    use_llm: bool = False


class TelegramClassifyRequest(StrictModel):
    limit: int = Field(default=100, ge=1, le=5000)
    use_llm: bool = False
    only_unclassified: bool = True


class TelegramCloseRequest(StrictModel):
    exit_price: PositivePrice
    closed_at: Optional[datetime] = None
    notes: Optional[Notes] = None


# Resolve the forward reference in TelegramCallDetail.
TelegramCallDetail.model_rebuild()
