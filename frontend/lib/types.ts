export type OptionType = "CE" | "PE";
export type LegAction = "BUY" | "SELL";
export type StrategyType = "bull_put_spread" | "bear_call_spread" | "iron_condor" | "straddle" | "strangle";
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
  data_source: "live" | "clickhouse" | "sample";
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
  data_source: "live" | "clickhouse" | "sample" | null;
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
}

export interface SignalDecision {
  params: Record<string, number>;
  computed: SignalDecisionComputed;
  reject_reason: string | null;
}

export interface SignalHistoryDetail extends SignalHistoryItem {
  signal_json: ScreenerSignal | null;
  decision_json: SignalDecision | null;
}

export interface ScreenerRunDetail extends ScreenerRun {
  signals: SignalHistoryDetail[];
}
