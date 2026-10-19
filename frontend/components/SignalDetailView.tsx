"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import SignalChartPanel from "./SignalChartPanel";
import CombinedStrategyChart from "./charts/CombinedStrategyChart";
import { decisionMetrics } from "@/lib/signalMetrics";
import { StatusBadge } from "./SignalHistoryRow";
import {
  fetchChartTransforms,
  fetchSignalHistoryDetail,
  fetchStockChartTransforms,
} from "@/lib/api";
import { CHART_INTERVALS, type ChartInterval, type ChartTransformsResponse } from "@/lib/charts";
import { formatDate, formatDateTime, formatStrategyLabel } from "@/lib/format";
import type { ScreenerSignal, SignalHistoryDetail, StrStraSignalJson } from "@/lib/types";

type ChartLeg = { strike: number; option_type: "CE" | "PE"; action: "BUY" | "SELL" };

const STR_STRA_TYPES = new Set(["straddle", "strangle", "adx_direction", "unified"]);

function isStrStra(t: SignalHistoryDetail["strategy_type"]): boolean {
  return !!t && STR_STRA_TYPES.has(t);
}

function legsOf(signal: SignalHistoryDetail): ChartLeg[] {
  const sj = signal.signal_json;
  if (!sj || !Array.isArray(sj.legs)) return [];
  return sj.legs.map((l) => ({ strike: l.strike, option_type: l.option_type, action: l.action }));
}

/** Reference price for the combined-premium chart's dashed line. For a short
 * straddle/strangle the lower breakeven is the natural reference; credit
 * spreads carry a screener-computed breakeven. */
function breakevenOf(signal: SignalHistoryDetail): number {
  const sj = signal.signal_json;
  if (!sj) return 0;
  if (isStrStra(signal.strategy_type)) {
    const s = sj as StrStraSignalJson;
    const atm = s.atm ?? 0;
    if (signal.strategy_type === "strangle") {
      return atm - s.step - (s.strangle_prem ?? 0);
    }
    return atm - (s.straddle_prem ?? 0);
  }
  return (sj as ScreenerSignal).risk_reward?.breakeven ?? 0;
}

export default function SignalDetailView({ historyId }: { historyId: number }) {
  const [detail, setDetail] = useState<SignalHistoryDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [charts, setCharts] = useState<ChartTransformsResponse | null>(null);
  const [chartsError, setChartsError] = useState<string | null>(null);
  const [intervalMinutes, setIntervalMinutes] = useState<ChartInterval>(5);

  useEffect(() => {
    let cancelled = false;
    fetchSignalHistoryDetail(historyId)
      .then((d) => {
        if (!cancelled) setDetail(d);
      })
      .catch(() => {
        if (!cancelled) setError("Could not load this signal — is the backend running?");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [historyId]);

  const symbol = detail?.symbol ?? null;
  const expiry = detail?.expiry ?? null;
  const legs = useMemo(() => (detail ? legsOf(detail) : []), [detail]);
  const stock = !!detail && isStrStra(detail.strategy_type);

  useEffect(() => {
    if (!detail || !symbol || !expiry) return;
    const sym = symbol;
    const exp = expiry;
    let cancelled = false;
    async function load() {
      try {
        const req = { symbol: sym, expiry: exp, legs };
        const data = stock
          ? await fetchStockChartTransforms(req, { interval: intervalMinutes })
          : await fetchChartTransforms(req, { interval: intervalMinutes });
        if (!cancelled) {
          setCharts(data);
          setChartsError(null);
        }
      } catch {
        if (!cancelled) {
          setChartsError(
            "Could not load chart data for this signal — is the backend logged in to Definedge, and do these contracts/expiry still exist in the chain?",
          );
        }
      }
    }
    load();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detail, symbol, expiry, stock, intervalMinutes]);

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center bg-[#0B0E11] text-sm text-[#8B96A5]">
        Loading signal…
      </div>
    );
  }

  if (error || !detail) {
    return (
      <div className="flex h-screen items-center justify-center bg-[#0B0E11] text-sm text-[#8B96A5]">
        {error ?? "Signal not found."}{" "}
        <Link href="/" className="ml-1 text-[#5B9BD5]">
          Back to dashboard
        </Link>
      </div>
    );
  }

  const metrics = decisionMetrics(detail);
  const breakeven = breakevenOf(detail);

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-[#0B0E11] text-[#E8EAED]">
      {/* TOP BAR */}
      <div
        className="flex flex-shrink-0 items-center gap-4 px-6 py-3"
        style={{ background: "#12161C", borderBottom: "1px solid #1F2630" }}
      >
        <Link
          href="/"
          className="flex items-center gap-1.5 rounded-[7px] border px-3 py-1.5 text-xs text-[#8B96A5]"
          style={{ borderColor: "#232C36" }}
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#8B96A5" strokeWidth="2">
            <path d="M15 18l-6-6 6-6" />
          </svg>
          Dashboard
        </Link>
        <div className="h-[22px] w-px bg-[#1F2630]" />
        <div className="text-[15px] font-semibold">Signal Detail</div>
        <div className="rounded-[5px] bg-[#1B2129] px-2 py-[3px] text-[11px] text-[#8B96A5]">
          Read-only · charts for manual decision-making
        </div>
        <div className="flex-grow" />
        <StatusBadge status={detail.status} />
        <span className="text-[11px] text-[#8B96A5]">
          <span className="font-semibold text-[#E8EAED]">
            {detail.symbol ?? "—"} · {detail.expiry ? formatDate(detail.expiry) : "—"} ·{" "}
            {detail.strategy_type ? formatStrategyLabel(detail.strategy_type) : "—"}
          </span>
        </span>
      </div>

      {/* BODY */}
      <div className="flex min-h-0 flex-grow overflow-hidden">
        {/* SIDEBAR */}
        <div
          className="flex w-[250px] flex-shrink-0 flex-col overflow-y-auto py-4"
          style={{ background: "#0E1116", borderRight: "1px solid #1A2028" }}
        >
          <div className="mx-4 rounded-[9px] border p-3" style={{ background: "#12161C", borderColor: "#1F2630" }}>
            <div className="mb-2 text-[10px] text-[#8B96A5]">SIGNAL</div>
            {detail.reason && (
              <div className="mb-2.5 text-[11px] leading-relaxed text-[#A7AFBA]">&quot;{detail.reason}&quot;</div>
            )}
            <div className="flex flex-col gap-[7px] text-[11.5px]">
              {metrics.map((m) => (
                <div key={m.label} className="flex justify-between">
                  <span className="text-[#8B96A5]">{m.label}</span>
                  <span className="mono font-semibold text-[#C7CDD6]">{m.value}</span>
                </div>
              ))}
            </div>
            <div className="mt-2.5 border-t pt-2.5 text-[10px] text-[#5B6675]" style={{ borderColor: "#1A2028" }}>
              {detail.generated_at ? formatDateTime(detail.generated_at) : "—"} · {detail.data_source ?? "unknown"}
            </div>
          </div>
        </div>

        {/* MAIN */}
        <div className="flex min-h-0 flex-grow flex-col overflow-y-auto px-6 py-4">
          {chartsError && (
            <div className="mb-3.5 rounded-[8px] border border-amber-900 bg-amber-950/60 px-3 py-2 text-xs text-amber-300">
              {chartsError}
            </div>
          )}

          <div className="mb-2 flex flex-shrink-0 items-center justify-between text-[10px] text-[#8B96A5]">
            <span>
              {charts
                ? `${detail.symbol ?? ""} ${detail.expiry ? formatDate(detail.expiry) : ""} · ${
                    detail.strategy_type ? formatStrategyLabel(detail.strategy_type) : ""
                  }${legs.length ? ` ${legs.map((l) => l.strike).join("/")}` : ""}`
                : "Resolving contracts for this signal…"}
            </span>
            <div className="flex items-center gap-2">
              <div className="flex gap-0.5 rounded-[7px] bg-[#171C23] p-[3px]">
                {CHART_INTERVALS.map((m) => (
                  <button
                    key={m}
                    onClick={() => setIntervalMinutes(m)}
                    className="rounded-[5px] px-2 py-1 text-[10px]"
                    style={{
                      background: intervalMinutes === m ? "#232C36" : "transparent",
                      color: intervalMinutes === m ? "#E8EAED" : "#8B96A5",
                      fontWeight: intervalMinutes === m ? 600 : 400,
                    }}
                  >
                    {m} Min
                  </button>
                ))}
              </div>
              {charts && (
                <span>as of {new Date(charts.as_of).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</span>
              )}
            </div>
          </div>

          {charts ? (
            <div
              className={`grid min-h-[480px] flex-grow gap-3.5 auto-rows-fr ${
                1 + charts.series.legs.length + (charts.series.combined ? 1 : 0) <= 4 ? "grid-cols-2" : "grid-cols-3"
              }`}
            >
              <SignalChartPanel series={charts.series.future} />
              {charts.series.legs.map((leg, i) => (
                <SignalChartPanel key={i} series={leg} />
              ))}
              {charts.series.combined && (
                <CombinedStrategyChart
                  premium={charts.series.combined}
                  future={charts.series.future}
                  breakeven={breakeven}
                />
              )}
            </div>
          ) : (
            !chartsError && (
              <div className="flex min-h-0 flex-grow items-center justify-center text-sm text-[#8B96A5]">
                Loading chart transforms…
              </div>
            )
          )}
        </div>
      </div>
    </div>
  );
}
