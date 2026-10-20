import type { Candle } from "./charts";

export type OptionType = "CE" | "PE";
export type LegAction = "BUY" | "SELL";
export type StrategyType = "bull_put_spread" | "bear_call_spread" | "iron_condor" | "straddle" | "strangle" | "adx_direction" | "unified";
export type AlertType =
  | "screener_signal"
  | "profit_target"
  | "max_loss"
  | "margin_call";
export type PositionStatus = "open" | "closed";

export interface Leg {
  strike: number;
  option_type: OptionType;
  action: LegAction;
  delta: number;
  iv: number; // fraction (0.15 = 15%)
  price?: number | null; // premium the signal was priced from
}

export interface Greeks {
  delta: number;
  theta: number;
  vega: number;
  gamma: number;
}

export interface RiskReward {
  max_profit: number;
  max_loss: number;
  breakeven: number;
  pop: number; // Probability of Profit, 0-1
  breakeven_lower?: number | null; // iron condor only (breakeven is the upper one)
}

export interface ScreenerSignal {
  signal_id: string;
  strategy_type: StrategyType;
  symbol: string;
  expiry: string; // ISO date
  legs: Leg[];
  greeks: Greeks;
  risk_reward: RiskReward;
  reason: string;
  generated_at: string; // ISO datetime
  data_source: "live" | "clickhouse" | "sample" | "openalgo";
  reason_source?: "template" | "llm";
}

export interface ScreenerStatus {
  state: "starting" | "ok" | "sample" | "degraded";
  data_source: "live" | "clickhouse" | "sample" | null;
  message: string;
  last_run: string | null;
}

export interface PositionLeg {
  id: number;
  strike: number;
  option_type: OptionType;
  action: LegAction;
  qty: number;
  entry_price: number;
  ltp: number;
  sl: number | null;
  tsl_trigger: number | null;
  tsl_step: number | null;
  target: number | null;
  roll_to_strike: number | null;
  closed_at: string | null;
  exit_price: number | null;
  realized_pnl: number | null;
  unrealized_pnl: number;
}

export interface PositionLegCreate {
  strike: number;
  option_type: OptionType;
  action: LegAction;
  qty: number;
  entry_price: number;
}

export interface Position {
  id: number;
  strategy_type: StrategyType;
  symbol: string;
  expiry: string;
  legs: PositionLeg[];
  entry_time: string;
  exit_time: string | null;
  status: PositionStatus;
  profit_target: number | null;
  max_loss: number | null;
  realized_pnl: number | null;
  unrealized_pnl: number;
  booked_pnl: number;
  notes?: string;
}

export interface Alert {
  id: number;
  alert_type: AlertType;
  strategy_type: StrategyType | null;
  symbol: string;
  message: string;
  is_read: boolean;
  created_at: string;
}

export interface BackfillStatus {
  contract_id: string;
  symbol: string;
  expiry: string;
  strike: number;
  option_type: OptionType;
  last_completed_date: string | null;
  status: "pending" | "in_progress" | "complete" | "failed";
}

export interface BrokerSessionInfo {
  broker: string;
  is_active: boolean;
  expires_at: string;
}

export interface AccountSummary {
  available_margin: number;
  used_margin: number;
  total_balance: number;
  nifty_spot: number;
  nifty_change_pct: number;
}

export interface IndexQuote {
  symbol: string;
  spot: number;
  change_pct: number;
}

export interface ScreenerRuleRef {
  label: string;
  value: string;
  tone: "green" | "blue" | "amber";
  note: string;
}

// --- Phase 3: payoff graph ---

export interface PayoffLeg {
  strike: number;
  option_type: OptionType;
  action: LegAction;
  qty: number;
  entry_price?: number; // omit for a not-yet-logged signal — backend prices it at today's live premium
  iv?: number; // fraction, e.g. 0.15 — omit/0 for a position leg (see current_price)
  current_price?: number; // position leg's live ltp, used to solve iv server-side when iv is omitted
}

export interface PayoffResponse {
  spot_range: number[];
  current_spot: number;
  at_expiry: number[];
  at_target: number[];
  breakevens: number[];
  max_profit: number;
  max_loss: number;
  target_date: string;
}

// --- Phase 4: option chain ---

export interface ChainLegQuote {
  strike: number;
  ltp: number;
  day_open: number;
  diff_pct: number;
}

export interface ChainRow {
  otm: number;
  strike: number;
  ce: ChainLegQuote | null;
  pe: ChainLegQuote | null;
  combined_ltp: number | null;
  combined_open: number | null;
  combined_diff_pct: number | null;
}

export interface ChainResponse {
  symbol: string;
  expiry: string;
  spot: number;
  atm_strike: number;
  updated_at: string;
  rows: ChainRow[];
}

// --- Phase 5: OI/PCR, session IV, Kelly sizing ---

export interface OiRow {
  otm: number;
  strike: number;
  call_oi: number | null;
  call_oi_change: number | null;
  put_oi: number | null;
  put_oi_change: number | null;
}

export interface SessionIvPoint {
  ts: string;
  atm_iv: number;
}

export interface OiPcrResponse {
  symbol: string;
  expiry: string;
  spot: number;
  atm_strike: number;
  as_of: string | null;
  call_oi_total: number;
  put_oi_total: number;
  oi_pcr: number | null;
  call_vol_total: number;
  put_vol_total: number;
  vol_pcr: number | null;
  rows: OiRow[];
  session_iv: SessionIvPoint[];
}

export interface KellySizingResponse {
  capital: number;
  assignment_exposure: number;
  exposure_pct_of_capital: number;
  premium_collected: number;
  managed_sl_risk: number;
  managed_sl_risk_pct_of_capital: number;
  over_allocated: boolean;
  open_leg_count: number;
}

// --- Admin: historical-data-download (proactive archival) ---

export interface FnoStock {
  symbol: string;
  lot_size: number;
  nearest_expiry: string | null;
}

export interface DownloadScope {
  equity: boolean;
  futures: boolean;
  options: boolean;
  strike_band: number;
}

export interface DownloadValidateItem {
  instrument_key: string;
  label: string;
  available: boolean;
  note: string | null;
}

export interface DownloadValidateResponse {
  symbol: string;
  clamped_start_date: string;
  clamped_end_date: string;
  items: DownloadValidateItem[];
  estimated_candle_calls: number;
  warnings: string[];
}

export interface DownloadJobStatus {
  id: number;
  symbol: string;
  start_date: string;
  end_date: string;
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
  total_items: number;
  completed_items: number;
  total_rows: number;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface DownloadJobItemStatus {
  instrument_key: string;
  status: "pending" | "done" | "failed" | "skipped";
  rows: number;
  error: string | null;
}

export interface DownloadCoverageRow {
  instrument_type: string;
  count: number;
  min_ts: string | null;
  max_ts: string | null;
}

// --- Signal history (screener signal + reject tracking) ---

export interface ScreenerRun {
  run_id: string;
  started_at: string;
  data_source: "live" | "clickhouse" | "sample" | "openalgo" | null;
  status: "ok" | "sample" | "degraded";
  message: string;
  signal_count: number;
  created_at: string;
}

export interface SignalHistoryItem {
  id: number;
  run_id: string;
  signal_id: string | null;
  strategy_type: StrategyType | null;
  symbol: string | null;
  expiry: string | null;
  status: "accepted" | "rejected";
  reject_reason: string | null;
  /** Unified-screener tier (PRIME / READY / WATCH); null for other strategies. */
  tier?: string | null;
  generated_at: string | null;
  data_source: string | null;
  reason: string | null;
  reason_source: string | null;
  created_at: string;
}

export interface SignalDecisionComputed {
  iv_rank: number | null;
  rsi: number | null;
  support: number | null;
  resistance: number | null;
  short_strike: number | null;
  short_delta: number | null;
  short_iv: number | null;
  pop: number | null;
  credit: number | null;
  max_loss: number | null;
  breakeven: number | null;
  breakeven_lower: number | null;
  net_delta: number | null;
  net_theta: number | null;
  net_vega: number | null;
  net_gamma: number | null;
  detail?: string | null;
  // --- str-stra signals (straddle/strangle/adx_direction/unified) ---
  adx?: number | null;
  adx_change_pct?: number | null;
  adx_trend?: string | null;
  plus_di?: number | null;
  minus_di?: number | null;
  dmi_trend?: string | null;
  candle_type?: string | null;
  volume_pct?: number | null;
  comment?: string | null;
  pattern?: string | null;
  fut_ltp?: number | null;
  atm?: number | null;
  ce_ltp?: number | null;
  pe_ltp?: number | null;
  spread_pct?: number | null;
  straddle_prem?: number | null;
  straddle_val?: number | null;
  strangle_prem?: number | null;
  strangle_val?: number | null;
  tier?: string | null;
  n_pass?: number | null;
  adx_pass?: boolean | null;
  candle_pass?: boolean | null;
  straddle_pass?: boolean | null;
  lot?: number | null;
}

export interface SignalDecision {
  params: Record<string, number>;
  computed: SignalDecisionComputed;
  reject_reason: string | null;
}

/** A single option leg stored inside a str-stra signal's `signal_json` —
 * carries the premium the signal was priced from (unlike `Leg`, which also
 * carries delta/IV from the Definedge pricing path). */
export interface StrStraLeg {
  strike: number;
  option_type: OptionType;
  action: LegAction;
  price: number | null;
}

/** The raw metrics dict persisted as a str-stra signal's `signal_json`. The
 * str-stra pipeline only receives LTPs from Definedge (no IV/greeks), so it
 * stores its computed fields directly rather than a full `ScreenerSignal`.
 * `legs` is added by the worker so the detail view can chart the underlying
 * + combined premium. */
export interface StrStraSignalJson {
  symbol: string;
  lot: number;
  step: number;
  lcp: number;
  pct_chg: number;
  adx: number;
  adx_chg_pct: number;
  adx_trend: string;
  dmi_trend: string;
  candle_type: string;
  comment: string;
  pattern: string;
  volume_pct: number;
  atm: number | null;
  fut_ltp: number | null;
  ce_ltp: number | null;
  pe_ltp: number | null;
  otm_ce: number | null;
  otm_pe: number | null;
  spread_pct: number | null;
  straddle_prem: number | null;
  strangle_prem: number | null;
  tier: string;
  n_pass: number;
  legs: StrStraLeg[];
  [key: string]: unknown;
}

export interface SignalHistoryDetail extends SignalHistoryItem {
  signal_json: ScreenerSignal | StrStraSignalJson | null;
  decision_json: SignalDecision | null;
}

export interface ScreenerRunDetail extends ScreenerRun {
  signals: SignalHistoryDetail[];
}

// --- Market breadth ---

export interface BreadthMetrics {
  advancers: number;
  decliners: number;
  unchanged: number;
  breadth_ratio: number | null;
  pct_above: Record<string, number | null>;
  total: number;
}

export interface BreadthSnapshot {
  bucket: "hour" | "day" | "week" | "month";
  ts: string;
  metrics: BreadthMetrics;
}

export interface BreadthResponse {
  universe: string;
  total: number;
  as_of: string | null;
  intraday: BreadthSnapshot[];
  daily: BreadthSnapshot[];
  weekly: BreadthSnapshot[];
  monthly: BreadthSnapshot[];
}

// --- Morning Daily Byte ---

export interface ByteSymbolCall {
  ticker: string;
  direction?: string | null;
  target?: number | null;
  stop_loss?: number | null;
  horizon?: string | null;
}

export interface ByteEvent {
  name: string;
  date?: string | null;
  impact?: string | null;
}

export interface ByteGlobalCue {
  instrument: string;
  direction?: string | null;
}

export interface DailyByteAnalysis {
  sentiment?: string | null;
  confidence?: number | null;
  nifty_level?: number | null;
  nifty_bias?: string | null;
  nifty_support: number[];
  nifty_resistance: number[];
  nifty_targets: number[];
  banknifty_level?: number | null;
  banknifty_bias?: string | null;
  banknifty_support: number[];
  banknifty_resistance: number[];
  symbols: ByteSymbolCall[];
  sectors: string[];
  events: ByteEvent[];
  global_cues: ByteGlobalCue[];
  strategy_type?: string | null;
  advance_decline?: string | null;
  fii_dii?: string | null;
  warnings: string[];
}

export interface DailyByteSource {
  group_slot: number;
  chat_username?: string | null;
  chat_title?: string | null;
  posted_at?: string | null;
  text: string;
  summary?: string | null;
  analysis?: DailyByteAnalysis | null;
  is_valid: boolean;
}

export interface DailyByteDigestResponse {
  date: string;
  market_bias?: string | null;
  summary?: string | null;
  top_symbols: ByteSymbolCall[];
  sources: DailyByteSource[];
}

// --- Telegram trade-call tracker -----------------------------------------
export interface TelegramCall {
  id: number;
  message_fk: number;
  chat_id: number;
  chat_username?: string | null;
  chat_title?: string | null;
  message_id: number;
  posted_at: string;
  symbol: string;
  symbol_raw?: string | null;
  display_name?: string | null;
  segment: string;
  exchange?: string | null;
  direction: "long" | "short" | "neutral";
  action: "BUY" | "SELL";
  option_type?: "CE" | "PE" | null;
  strike?: number | null;
  expiry?: string | null;
  expiry_text?: string | null;
  entry_low?: number | null;
  entry_high?: number | null;
  entry_price?: number | null;
  stop_loss?: number | null;
  target_1?: number | null;
  target_2?: number | null;
  target_3?: number | null;
  cmp?: number | null;
  horizon?: string | null;
  status: "open" | "target_hit" | "sl_hit" | "closed" | "expired";
  status_reason?: string | null;
  status_source?: "message" | "price" | "manual" | null;
  exit_price?: number | null;
  closed_at?: string | null;
  realized_pct?: number | null;
  r_multiple?: number | null;
  confidence?: number | null;
  method: string;
  is_valid: boolean;
  tracking_status?: string | null;
  tracking_message?: string | null;
  last_checked_at?: string | null;
  last_price?: number | null;
  notes?: string | null;
  source_text?: string | null;
  source_url?: string | null;
  created_at: string;
  updated_at: string;
}

export interface TelegramCallEvent {
  id: number;
  call_id?: number | null;
  message_fk: number;
  chat_id: number;
  message_id: number;
  posted_at: string;
  event_type: "alert" | "target_hit" | "sl_hit" | "closed" | "partial_exit" | "trail" | "update";
  symbol?: string | null;
  option_type?: "CE" | "PE" | null;
  strike?: number | null;
  expiry?: string | null;
  price?: number | null;
  target_value?: number | null;
  realized_pct?: number | null;
  note?: string | null;
  method: string;
  is_valid: boolean;
  text?: string | null;
}

export interface TelegramArchiveMessage {
  id: number;
  chat_id: number;
  chat_username?: string | null;
  chat_title?: string | null;
  message_id: number;
  posted_at: string;
  posted_local?: string | null;
  sender_name?: string | null;
  sender_username?: string | null;
  text: string;
  media_type?: string | null;
  views?: number | null;
  forwards?: number | null;
  reply_to_msg_id?: number | null;
  category?: string | null;
  method?: string | null;
  confidence?: number | null;
  call_count: number;
}

export interface TelegramCallDetail extends TelegramCall {
  extraction?: Record<string, unknown> | null;
  events: TelegramCallEvent[];
  thread: TelegramArchiveMessage[];
}

export interface TelegramChatSummary {
  chat_id: number;
  username?: string | null;
  title?: string | null;
  message_count: number;
  first_message_at?: string | null;
  last_message_at?: string | null;
  call_count: number;
  open_count: number;
  target_hit_count: number;
  sl_hit_count: number;
  closed_count: number;
}

export interface TelegramCallStats {
  total_calls: number;
  open: number;
  target_hit: number;
  sl_hit: number;
  closed: number;
  expired: number;
  win_rate?: number | null;
  avg_realized_pct?: number | null;
  tracked: number;
  unresolved: number;
  total_messages: number;
  classified_messages: number;
  last_sync_at?: string | null;
}

export interface TelegramJobStatus {
  state: "starting" | "running" | "ok" | "degraded" | "error";
  message: string;
  last_run?: string | null;
  stats?: Record<string, number>;
}

export interface TelegramStatusResponse {
  sync: TelegramJobStatus;
  classify: TelegramJobStatus;
  track: TelegramJobStatus;
}

export interface TelegramCallsPage {
  items: TelegramCall[];
  total: number;
  limit: number;
  offset: number;
}

export interface TelegramMessagesPage {
  items: TelegramArchiveMessage[];
  total: number;
  limit: number;
  offset: number;
}

export interface TelegramCallChartLevels {
  entry?: number | null;
  entry_low?: number | null;
  entry_high?: number | null;
  stop_loss?: number | null;
  targets: number[];
}

export interface TelegramCallChartResponse {
  call_id: number;
  label: string;
  trading_symbol: string;
  exchange: string;
  strike?: number | null;
  expiry?: string | null;
  /** Call/contract metadata — which fields matter depends on the segment. */
  segment?: string | null;
  instrument_type?: string | null;
  underlying?: string | null;
  lot_size?: number | null;
  tick_size?: number | null;
  option_type?: string | null;
  direction?: string | null;
  action?: string | null;
  status?: string | null;
  posted_at?: string | null;
  /** UTC seconds, used to anchor the vertical "call posted" line. */
  posted_at_utc_seconds?: number | null;
  interval_minutes: number;
  as_of: string;
  levels: TelegramCallChartLevels;
  ltp?: number | null;
  first_candle_at?: string | null;
  last_candle_at?: string | null;
  candles: Candle[];
}
