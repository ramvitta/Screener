import type {
  Alert,
  BackfillStatus,
  BrokerSessionInfo,
  ChainResponse,
  DownloadCoverageRow,
  DownloadJobItemStatus,
  DownloadJobStatus,
  DownloadScope,
  DownloadValidateResponse,
  FnoStock,
  IndexQuote,
  KellySizingResponse,
  OiPcrResponse,
  PayoffLeg,
  PayoffResponse,
  Position,
  PositionLeg,
  ScreenerSignal,
  ScreenerStatus,
  StrategyType,
} from "./types";
import type { SignalChartsResponse } from "./charts";

const DEFAULT_LOT_QTY = 1;

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
export const WS_BASE_URL =
  process.env.NEXT_PUBLIC_WS_BASE_URL ?? "ws://localhost:8000";

// Static bearer token for the local API (backend/.env API_AUTH_TOKEN). It is a
// NEXT_PUBLIC_ variable, so it is visible to anyone who can open this page —
// acceptable for a single-user app bound to localhost, NOT for a public deploy.
const API_AUTH_TOKEN = process.env.NEXT_PUBLIC_API_AUTH_TOKEN ?? "";

function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  return API_AUTH_TOKEN ? { ...extra, Authorization: `Bearer ${API_AUTH_TOKEN}` } : extra;
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, { cache: "no-store", headers: authHeaders() });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

async function postJson<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

async function patchJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method: "PATCH",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

// unrealized_pnl / booked_pnl now come pre-computed from the backend (see
// backend/app/api/routes/positions.py::_row_to_position), which is the
// source of truth for live LTP too — no client-side P&L recompute needed.
export async function fetchPositions(): Promise<Position[]> {
  return getJson<Position[]>("/api/v1/positions");
}

/** Best-effort live LTP refresh for all open legs (no-ops quietly if the
 * Definedge session isn't active) — call on an interval from the Positions
 * & P&L tab. Returns the refreshed position list. */
export async function refreshPositionLtp(): Promise<Position[]> {
  return postJson<Position[]>("/api/v1/positions/refresh-ltp");
}

export async function updatePositionLeg(
  positionId: number,
  legId: number,
  updates: Partial<Pick<PositionLeg, "sl" | "tsl_trigger" | "tsl_step" | "target" | "roll_to_strike">>,
): Promise<Position> {
  return patchJson<Position>(`/api/v1/positions/${positionId}/legs/${legId}`, updates);
}

export async function exitPositionLeg(positionId: number, legId: number, exitPrice: number): Promise<Position> {
  return postJson<Position>(`/api/v1/positions/${positionId}/legs/${legId}/exit`, { exit_price: exitPrice });
}

export async function exitAllLegs(positionId: number, exitPrice: number): Promise<Position> {
  return postJson<Position>(`/api/v1/positions/${positionId}/exit-all`, { exit_price: exitPrice });
}

export async function fetchScreenerSignals(): Promise<ScreenerSignal[]> {
  return getJson<ScreenerSignal[]>("/api/v1/screener/signals");
}

export async function fetchAlerts(): Promise<Alert[]> {
  const raw = await getJson<
    { id: number; alert_type: Alert["alert_type"]; strategy_type: Alert["strategy_type"]; symbol: string; payload: Record<string, unknown>; is_read: boolean; created_at: string }[]
  >("/api/v1/alerts");
  return raw.map((a) => ({
    id: a.id,
    alert_type: a.alert_type,
    strategy_type: a.strategy_type,
    symbol: a.symbol,
    message: typeof a.payload?.reason === "string" ? (a.payload.reason as string) : `${a.alert_type} for ${a.symbol}`,
    is_read: a.is_read,
    created_at: a.created_at,
  }));
}

export async function fetchBackfillStatus(): Promise<BackfillStatus[]> {
  return getJson<BackfillStatus[]>("/api/v1/backfill/status");
}

/** Real GET /api/v1/market/indices (see backend/app/api/routes/market.py) —
 * live NIFTY spot quote, replacing the old hardcoded mockIndexQuotes. Empty
 * array (not thrown) when Definedge isn't authenticated yet, so the TopBar
 * just shows no index rather than an error. */
export async function fetchIndexQuotes(): Promise<IndexQuote[]> {
  try {
    return await getJson<IndexQuote[]>("/api/v1/market/indices");
  } catch {
    return [];
  }
}

export async function fetchHealth(): Promise<{ status: string; scheduler_running: boolean }> {
  return getJson("/api/v1/health");
}

export async function fetchBrokerSession(): Promise<BrokerSessionInfo | null> {
  const raw = await getJson<{ is_active?: boolean; expires_at?: string }>("/api/v1/auth/definedge/session");
  if (!raw.is_active) return null;
  return { broker: "definedge", is_active: true, expires_at: raw.expires_at ?? "" };
}

/** Data provenance/health of the screener: "ok" (live data), "sample" (dev
 * fixture — NOT real prices) or "degraded" (no real data, no signals). */
export async function fetchScreenerStatus(): Promise<ScreenerStatus> {
  return getJson<ScreenerStatus>("/api/v1/screener/status");
}

/** Forces a fresh Definedge session via TOTP auto-login (POST
 * /api/v1/auth/definedge/relogin — see backend/app/api/routes/auth.py). The
 * backend already logs in automatically at startup and on an hourly
 * schedule; this is only for the rare case that needs a manual nudge (e.g.
 * the TOTP secret was rotated). */
export async function triggerRelogin(): Promise<{ expires_at: string }> {
  return postJson("/api/v1/auth/definedge/relogin");
}

/** Charts for a specific screener signal's actual legs — the underlying
 * future, each leg's own contract, and the combined net spread value. */
export async function fetchSignalCharts(
  signal: Pick<ScreenerSignal, "symbol" | "expiry" | "legs">,
  intervalMinutes: number = 1,
): Promise<SignalChartsResponse> {
  const res = await fetch(`${API_BASE_URL}/api/v1/charts/signal?interval=${intervalMinutes}`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      symbol: signal.symbol,
      expiry: signal.expiry,
      legs: signal.legs.map((leg) => ({ strike: leg.strike, option_type: leg.option_type, action: leg.action })),
    }),
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`charts/signal -> ${res.status}`);
  return res.json();
}

/** Real GET /limits passthrough (see backend/app/api/routes/account.py). Uses
 * Definedge's `cash` field as the headline available-margin figure. */
export async function fetchAvailableMargin(): Promise<number | null> {
  const raw = await getJson<{ cash?: string }>("/api/v1/account/margin");
  if (raw.cash === undefined) return null;
  const value = Number(raw.cash);
  return Number.isFinite(value) ? value : null;
}

/** Logs a screener signal as a manually-executed position (POST
 * /api/v1/positions per SRS_Architecture.md §4.6). Lot quantity defaults to
 * 1 since the screener signal doesn't carry sizing — the user confirms/edits
 * the real fill in the broker terminal per the read-only execution model. */
export async function createPosition(signal: ScreenerSignal): Promise<void> {
  if (signal.data_source === "sample") throw new Error("Refusing to log a position from SAMPLE data");
  // entry_price must be the premium the signal was priced from — logging 0
  // would make every P&L figure for the position wrong.
  if (signal.legs.some((leg) => !leg.price || leg.price <= 0)) {
    throw new Error("Signal has no leg prices; log this position from the Chain & Entry tab instead");
  }
  const res = await fetch(`${API_BASE_URL}/api/v1/positions`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      strategy_type: signal.strategy_type,
      symbol: signal.symbol,
      expiry: signal.expiry,
      legs: signal.legs.map((leg) => ({
        strike: leg.strike,
        option_type: leg.option_type,
        action: leg.action,
        qty: DEFAULT_LOT_QTY,
        entry_price: leg.price,
      })),
      entry_time: new Date().toISOString(),
    }),
  });
  if (!res.ok) throw new Error(`create position -> ${res.status}`);
}

/** Generic version of createPosition for an arbitrary leg combo (e.g. a
 * straddle/strangle logged from the Chain & Entry tab, see
 * PositionCreate in backend/app/models/schemas.py). Still just a log entry
 * — read-only execution model, no broker order is placed. */
export async function createPositionFromLegs(
  strategyType: StrategyType,
  symbol: string,
  expiry: string,
  legs: { strike: number; option_type: "CE" | "PE"; action: "BUY" | "SELL"; qty: number; entry_price: number }[],
): Promise<Position> {
  return postJson<Position>("/api/v1/positions", {
    strategy_type: strategyType,
    symbol,
    expiry,
    legs,
    entry_time: new Date().toISOString(),
  });
}

/** POST /api/v1/analytics/payoff (Phase 3) — P&L curves at expiry and at a
 * target date for an arbitrary leg combo (a live signal, an open position,
 * or a what-if the user hasn't logged yet). `targetDate` omitted means
 * "today". */
export async function fetchPayoff(
  symbol: string,
  expiry: string,
  legs: PayoffLeg[],
  targetDate?: string,
): Promise<PayoffResponse> {
  return postJson<PayoffResponse>("/api/v1/analytics/payoff", {
    symbol,
    expiry,
    legs,
    target_date: targetDate,
  });
}

/** GET /api/v1/chain/{symbol} (Phase 4) — real ATM±depth option chain. */
export async function fetchOptionChain(symbol: string, depth = 5, expiry?: string): Promise<ChainResponse> {
  const params = new URLSearchParams({ depth: String(depth) });
  if (expiry) params.set("expiry", expiry);
  return getJson<ChainResponse>(`/api/v1/chain/${symbol}?${params.toString()}`);
}

/** GET /api/v1/analytics/oi-pcr (Phase 5) — real OI/volume (from the
 * historical-candle endpoint, since live quotes carry no OI field) plus a
 * real intraday ATM-IV series for the same session. */
export async function fetchOiPcr(symbol = "NIFTY", depth = 5): Promise<OiPcrResponse> {
  return getJson<OiPcrResponse>(`/api/v1/analytics/oi-pcr?symbol=${symbol}&depth=${depth}`);
}

/** GET /api/v1/analytics/kelly (Phase 5) — derived purely from the user's
 * own open legs + a capital figure they supply (no broker call, no
 * fabricated default capital). */
export async function fetchKellySizing(capital: number): Promise<KellySizingResponse> {
  return getJson<KellySizingResponse>(`/api/v1/analytics/kelly?capital=${capital}`);
}

// --- Admin: historical-data-download (proactive archival) ---

export async function fetchFnoStocks(): Promise<FnoStock[]> {
  return getJson<FnoStock[]>("/api/v1/admin/history/fno-stocks");
}

export async function validateDownload(
  symbol: string,
  startDate: string,
  endDate: string,
  scope: DownloadScope,
): Promise<DownloadValidateResponse> {
  return postJson<DownloadValidateResponse>("/api/v1/admin/history/validate", {
    symbol,
    start_date: startDate,
    end_date: endDate,
    scope,
  });
}

export async function createDownloadJob(
  symbol: string,
  startDate: string,
  endDate: string,
  scope: DownloadScope,
): Promise<DownloadJobStatus> {
  return postJson<DownloadJobStatus>("/api/v1/admin/history/jobs", {
    symbol,
    start_date: startDate,
    end_date: endDate,
    scope,
  });
}

export async function fetchDownloadJobs(): Promise<DownloadJobStatus[]> {
  return getJson<DownloadJobStatus[]>("/api/v1/admin/history/jobs");
}

export async function fetchDownloadJob(jobId: number): Promise<DownloadJobStatus> {
  return getJson<DownloadJobStatus>(`/api/v1/admin/history/jobs/${jobId}`);
}

export async function fetchDownloadJobItems(jobId: number): Promise<DownloadJobItemStatus[]> {
  return getJson<DownloadJobItemStatus[]>(`/api/v1/admin/history/jobs/${jobId}/items`);
}

export async function stopDownloadJob(jobId: number): Promise<DownloadJobStatus> {
  return postJson<DownloadJobStatus>(`/api/v1/admin/history/jobs/${jobId}/stop`);
}

export async function fetchDownloadCoverage(symbol: string): Promise<DownloadCoverageRow[]> {
  return getJson<DownloadCoverageRow[]>(`/api/v1/admin/history/coverage?symbol=${symbol}`);
}
