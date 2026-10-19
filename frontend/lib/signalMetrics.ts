import { formatCurrency, formatPct } from "@/lib/format";
import type { SignalHistoryDetail } from "@/lib/types";

/**
 * Human-readable decision metrics for a stored signal, keyed by strategy type.
 * Str-stra signals (straddle/strangle/adx_direction/unified) carry their raw
 * computed fields in `decision_json.computed`; credit spreads carry IV/POP/
 * RSI/greeks there. Kept in a plain (non-"use client") module so it can be
 * imported by any client component without crossing a client-reference
 * boundary.
 */
export function decisionMetrics(signal: SignalHistoryDetail): { label: string; value: string }[] {
  const c = signal.decision_json?.computed;
  if (!c) return [];
  const num = <T,>(v: T | null | undefined, fmt: (n: T) => string) => (v === null || v === undefined ? "—" : fmt(v));
  const signed = (n: number) => `${n >= 0 ? "+" : ""}${n.toFixed(1)}%`;
  const vol = (n: number) => `${n >= 0 ? "+" : ""}${n.toFixed(0)}%`;
  const flag = (b: boolean) => (b ? "✓" : "✗");

  const t = signal.strategy_type;
  if (t === "straddle" || t === "strangle") {
    return [
      { label: "ATM", value: num(c.atm, (n) => n.toFixed(1)) },
      { label: "Spread", value: num(c.spread_pct, (n) => `${n.toFixed(1)}%`) },
      { label: "Premium", value: num(t === "strangle" ? c.strangle_prem : c.straddle_prem, formatCurrency) },
      { label: "Premium Value", value: num(t === "strangle" ? c.strangle_val : c.straddle_val, formatCurrency) },
      { label: "ADX", value: `${num(c.adx, (n) => n.toFixed(1))} ${c.adx_trend ?? ""}`.trim() },
      { label: "Volume", value: num(c.volume_pct, vol) },
    ];
  }
  if (t === "adx_direction") {
    return [
      { label: "ADX", value: `${num(c.adx, (n) => n.toFixed(1))} ${c.adx_trend ?? ""}`.trim() },
      { label: "ADX Chg%", value: num(c.adx_change_pct, signed) },
      { label: "Candle", value: `${c.candle_type ?? "—"}${c.comment ? ` · ${c.comment}` : ""}` },
      { label: "Volume", value: num(c.volume_pct, vol) },
      { label: "+DI / −DI", value: `${num(c.plus_di, (n) => n.toFixed(1))} / ${num(c.minus_di, (n) => n.toFixed(1))}` },
      { label: "Fut LTP", value: num(c.fut_ltp, (n) => n.toFixed(2)) },
    ];
  }
  if (t === "unified") {
    return [
      { label: "Tier", value: `${c.tier ?? "—"} (${c.n_pass ?? 0}/3)` },
      { label: "ADX", value: `${num(c.adx_pass, flag)} · ${num(c.adx, (n) => n.toFixed(1))}` },
      { label: "Candle", value: `${num(c.candle_pass, flag)} · ${c.comment ?? "—"}` },
      { label: "Parity", value: `${num(c.straddle_pass, flag)} · ${num(c.spread_pct, (n) => `${n.toFixed(1)}%`)}` },
      { label: "ATM", value: num(c.atm, (n) => n.toFixed(1)) },
      { label: "Volume", value: num(c.volume_pct, vol) },
    ];
  }

  return [
    { label: "IV Rank", value: num(c.iv_rank, (n) => `${n.toFixed(0)}%`) },
    { label: "POP", value: num(c.pop, formatPct) },
    { label: "Credit", value: num(c.credit, formatCurrency) },
    { label: "Max loss", value: num(c.max_loss, formatCurrency) },
    { label: "RSI", value: num(c.rsi, (n) => n.toFixed(0)) },
    { label: "Breakeven", value: num(c.breakeven, (n) => n.toFixed(0)) },
  ];
}
