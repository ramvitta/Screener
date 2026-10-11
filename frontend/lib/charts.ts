export interface Candle {
  ts: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  oi: number | null;
}

export interface ChartSeries {
  label: string;
  trading_symbol: string;
  strike: number | null;
  expiry: string | null;
  ltp: number;
  day_open: number | null;
  day_high: number | null;
  day_low: number | null;
  prev_close: number | null;
  change: number | null;
  change_pct: number | null;
  candles: Candle[];
}

export interface NiftyChartsResponse {
  as_of: string;
  atm_strike: number;
  options_expiry: string;
  interval_minutes: number;
  series: {
    future: ChartSeries;
    spot: ChartSeries;
    ce: ChartSeries;
    pe: ChartSeries;
  };
}

export const CHART_INTERVALS = [1, 3, 5, 10, 15, 30, 60] as const;
export type ChartInterval = (typeof CHART_INTERVALS)[number];

export interface SignalChartsResponse {
  as_of: string;
  interval_minutes: number;
  series: {
    future: ChartSeries;
    legs: ChartSeries[];
    combined: ChartSeries | null;
  };
}
