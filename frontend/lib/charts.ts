export interface Candle {
  ts: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  oi: number | null;
}

export const CHART_INTERVALS = [1, 3, 5, 10, 15, 30, 60] as const;
export type ChartInterval = (typeof CHART_INTERVALS)[number];

/** openalgo-charts `Bar` — `time` is UTC seconds (integer). */
export interface OpenAlgoBar {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
  oi?: number;
}

/** openalgo-charts `PointFigureColumn` (a `Bar` + `boxSize` + `boxes`). */
export interface PointFigureColumn extends OpenAlgoBar {
  boxSize: number;
  boxes: number;
}

/** Per-series payload from POST /api/v1/charts/transforms. */
export interface ChartTransformSeries {
  label: string;
  trading_symbol: string;
  strike: number | null;
  expiry: string | null;
  reference_price: number | null;
  box_size: number | null;
  /** Effective box percentage actually used (after the backend's brick cap
   *  auto-widens it); this may exceed the requested value for volatile series. */
  box_pct: number | null;
  candles: Candle[];
  renko: OpenAlgoBar[];
  pnf: PointFigureColumn[];
}

export interface ChartTransformsResponse {
  as_of: string;
  interval_minutes: number;
  reversal: number;
  series: {
    future: ChartTransformSeries;
    legs: ChartTransformSeries[];
    combined: ChartTransformSeries | null;
  };
}

/** Per-panel chart-type toggle. P&F is the default (FEATURE_CHARTS_PLAN.md §5.6). */
export const CHART_TYPES = ["point-figure", "renko", "candlestick"] as const;
export type ChartType = (typeof CHART_TYPES)[number];
export const DEFAULT_CHART_TYPE: ChartType = "point-figure";

/** Box-size presets (% of reference price) and per-instrument defaults (FEATURE_CHARTS_PLAN.md §8). */
export const BOX_PRESETS = [0.1, 0.25, 0.5, 1, 3] as const;
export const DEFAULT_FUTURE_BOX_PCT = 0.1;
export const DEFAULT_OPTION_BOX_PCT = 1.0;
export const DEFAULT_COMBINED_BOX_PCT = 0.5;
export const DEFAULT_REVERSAL = 3;

const IST_OFFSET_SECONDS = 5 * 3600 + 30 * 60; // Asia/Kolkata is a fixed UTC+5:30 (no DST).

/** Convert a backend `Candle.ts` (naive Asia/Kolkata wall-clock ISO string) to
 * the UTC-seconds integer openalgo-charts uses for its `time` axis. Parsing is
 * explicit (never relies on the host locale/timezone) and mirrors the backend
 * `to_utc_seconds`, so both sides agree on the time axis. */
export function toUtcSeconds(ts: string): number {
  const [datePart, timePart] = ts.split("T");
  const [y, m, d] = datePart.split("-").map(Number);
  const clock = timePart.split(":");
  const hh = Number(clock[0]);
  const mm = Number(clock[1]);
  const ss = Number(clock[2] ?? 0);
  return Math.floor((Date.UTC(y, m - 1, d, hh, mm, ss) - IST_OFFSET_SECONDS * 1000) / 1000);
}

/** Feed-adapter: backend `Candle[]` (naive IST `ts`) → openalgo-charts `Bar[]`
 * (`time` = UTC seconds). This is the edge conversion openalgo-charts expects
 * (ARCHITECTURE.md §4.0), keeping the heavy chart library out of `lib/`. */
export function toOpenAlgoBars(candles: readonly Candle[]): OpenAlgoBar[] {
  return candles.map((c) => ({
    time: toUtcSeconds(c.ts),
    open: c.open,
    high: c.high,
    low: c.low,
    close: c.close,
    volume: c.volume,
    oi: c.oi ?? undefined,
  }));
}

/** openalgo-charts `LinePoint` — `time` is UTC seconds, `value` is the y value. */
export interface OpenAlgoLinePoint {
  time: number;
  value: number;
}

/** Feed-adapter: backend `Candle[]` → openalgo-charts `LinePoint[]` using the
 * close price as the y value. Used for the combined-premium / underlying
 * strategy line view (net premium and future are single values over time, so
 * they draw as a line, not boxes). */
export function toOpenAlgoLine(candles: readonly Candle[]): OpenAlgoLinePoint[] {
  return candles.map((c) => ({ time: toUtcSeconds(c.ts), value: c.close }));
}
