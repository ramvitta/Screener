"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import CandlestickChart from "./charts/CandlestickChart";
import { fetchScreenerSignals, fetchSignalCharts } from "@/lib/api";
import { daysToExpiry, formatCurrency, formatDate, formatPct, formatRatio, formatStrategyLabel, riskAdjustedReturn } from "@/lib/format";
import type { ScreenerSignal } from "@/lib/types";
import { CHART_INTERVALS, type ChartInterval, type SignalChartsResponse } from "@/lib/charts";

const STRATEGY_LABELS: ScreenerSignal["strategy_type"][] = ["bull_put_spread", "bear_call_spread", "iron_condor"];
const CHART_POLL_MS = 30_000;

export default function ContractDetailView({ signalId }: { signalId: string }) {
  const [signals, setSignals] = useState<ScreenerSignal[]>([]);
  const [loading, setLoading] = useState(true);
  const [charts, setCharts] = useState<SignalChartsResponse | null>(null);
  const [chartsError, setChartsError] = useState<string | null>(null);
  const [intervalMinutes, setIntervalMinutes] = useState<ChartInterval>(1);

  useEffect(() => {
    let cancelled = false;
    fetchScreenerSignals()
      .then((s) => {
        if (!cancelled) setSignals(s);
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const signal = useMemo(
    () => signals.find((s) => s.signal_id === signalId) ?? signals[0],
    [signals, signalId],
  );

  useEffect(() => {
    if (!signal) return;
    let cancelled = false;
    let isFirstRun = true;

    async function poll() {
      if (isFirstRun) {
        setCharts(null);
        isFirstRun = false;
      }
      try {
        const data = await fetchSignalCharts(signal!, intervalMinutes);
        if (!cancelled) {
          setCharts(data);
          setChartsError(null);
        }
      } catch {
        if (!cancelled) {
          setChartsError(
            "Could not load live charts for this signal's legs — is the backend logged in to Definedge, and do these strikes/expiry still exist in the current chain?",
          );
        }
      }
    }

    poll();
    const interval = setInterval(poll, CHART_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signal?.signal_id, intervalMinutes]);

  const expiryChips = useMemo(() => {
    const seen = new Set<string>();
    const chips: { symbol: string; expiry: string }[] = [];
    for (const s of signals) {
      const key = `${s.symbol}|${s.expiry}`;
      if (!seen.has(key)) {
        seen.add(key);
        chips.push({ symbol: s.symbol, expiry: s.expiry });
      }
    }
    return chips;
  }, [signals]);

  if (!signal) {
    return (
      <div className="flex h-screen items-center justify-center bg-[#0B0E11] text-sm text-[#8B96A5]">
        {loading ? "Loading signals…" : "No signal found."}{" "}
        <Link href="/" className="ml-1 text-[#5B9BD5]">
          Back to dashboard
        </Link>
      </div>
    );
  }

  const rar = riskAdjustedReturn(signal.risk_reward);

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-[#0B0E11] text-[#E8EAED]">
      {/* TOP BAR / BREADCRUMB */}
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
        <div className="text-[15px] font-semibold">Contract &amp; Trade Detail</div>
        <div className="rounded-[5px] bg-[#1B2129] px-2 py-[3px] text-[11px] text-[#8B96A5]">
          Read-only · charts for manual decision-making
        </div>
        <div className="flex-grow" />
        <span className="text-[11px] text-[#8B96A5]">
          Selected:{" "}
          <span className="font-semibold text-[#E8EAED]">
            {signal.symbol} · {formatDate(signal.expiry)} · {formatStrategyLabel(signal.strategy_type)}{" "}
            {signal.legs.map((l) => l.strike).join("/")}
          </span>
        </span>
        <button className="rounded-[7px] bg-[#1F8F5C] px-4 py-2 text-xs font-semibold text-white">
          Log Position
        </button>
      </div>

      <div className="flex min-h-0 flex-grow overflow-hidden">
        {/* SIDEBAR */}
        <div
          className="flex w-[250px] flex-shrink-0 flex-col overflow-y-auto py-3.5"
          style={{ background: "#0E1116", borderRight: "1px solid #1A2028" }}
        >
          {expiryChips.map((c) => {
            const isSelected = c.symbol === signal.symbol && c.expiry === signal.expiry;
            return (
              <div
                key={`${c.symbol}|${c.expiry}`}
                className="flex items-center justify-between px-4 py-2.5"
                style={isSelected ? { background: "#171C23", borderLeft: "3px solid #3DD68C", paddingLeft: "13px" } : { color: "#8B96A5", paddingLeft: "19px" }}
              >
                <span className={`text-[12.5px] ${isSelected ? "font-semibold" : ""}`}>
                  {c.symbol} · {formatDate(c.expiry)}
                </span>
                <span className="mono text-[10px]" style={{ color: isSelected ? "#3DD68C" : undefined }}>
                  DTE {daysToExpiry(c.expiry)}
                </span>
              </div>
            );
          })}

          <div className="mx-4 my-3 h-px bg-[#1A2028]" />

          <div className="mx-4 rounded-[9px] border p-3" style={{ background: "#12161C", borderColor: "#1F2630" }}>
            <div className="mb-2 text-[10px] text-[#8B96A5]">SELECTED SPREAD</div>
            <div className="flex flex-col gap-[7px] text-[11.5px]">
              <div className="flex justify-between">
                <span className="text-[#8B96A5]">POP</span>
                <span className="mono font-bold text-[#3DD68C]">{formatPct(signal.risk_reward.pop)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-[#8B96A5]">Credit/Max Loss</span>
                <span className="mono">
                  {formatCurrency(signal.risk_reward.max_profit)} / {formatCurrency(signal.risk_reward.max_loss)}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-[#8B96A5]">RAR</span>
                <span className="mono font-bold text-[#F0B429]">{formatRatio(rar)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-[#8B96A5]">Breakeven</span>
                <span className="mono">{signal.risk_reward.breakeven.toLocaleString("en-IN")}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-[#8B96A5]">Net Δ</span>
                <span className="mono">{signal.greeks.delta.toFixed(2)}</span>
              </div>
            </div>
            <div className="mt-2.5 border-t pt-2.5 text-[11px] leading-relaxed text-[#A7AFBA]" style={{ borderColor: "#1A2028" }}>
              &quot;{signal.reason}&quot;
            </div>
          </div>
        </div>

        {/* MAIN */}
        <div className="flex min-h-0 flex-grow flex-col overflow-y-auto px-6 py-4">
          <div className="mb-3.5 flex w-fit items-center gap-1 rounded-[9px] border p-[5px]" style={{ background: "#12161C", borderColor: "#1F2630" }}>
            {STRATEGY_LABELS.map((strat) => (
              <div
                key={strat}
                className="rounded-[6px] px-4.5 py-2 text-[12.5px]"
                style={
                  strat === signal.strategy_type
                    ? { background: "#1F8F5C", color: "#fff", fontWeight: 600 }
                    : { color: "#8B96A5" }
                }
              >
                {formatStrategyLabel(strat)}
              </div>
            ))}
          </div>

          {chartsError && (
            <div className="mb-3.5 rounded-[8px] border border-amber-900 bg-amber-950/60 px-3 py-2 text-xs text-amber-300">
              {chartsError}
            </div>
          )}

          <div className="mb-2 flex flex-shrink-0 items-center justify-between text-[10px] text-[#8B96A5]">
            <span>
              {charts
                ? `${signal.symbol} ${formatDate(signal.expiry)} · ${formatStrategyLabel(signal.strategy_type)} ${signal.legs
                    .map((l) => l.strike)
                    .join("/")} ${signal.legs[0]?.option_type ?? ""}`
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
            <div className="grid min-h-0 flex-grow grid-cols-2 grid-rows-2 gap-3.5">
              <div className="flex min-h-0 flex-col rounded-[10px] border p-3" style={{ background: "#12161C", borderColor: "#1F2630" }}>
                <CandlestickChart series={charts.series.future} />
              </div>
              {charts.series.legs[0] && (
                <div className="flex min-h-0 flex-col rounded-[10px] border p-3" style={{ background: "#12161C", borderColor: "#1F2630" }}>
                  <CandlestickChart series={charts.series.legs[0]} />
                </div>
              )}
              {charts.series.legs[1] && (
                <div className="flex min-h-0 flex-col rounded-[10px] border p-3" style={{ background: "#12161C", borderColor: "#1F2630" }}>
                  <CandlestickChart series={charts.series.legs[1]} />
                </div>
              )}
              {charts.series.combined && (
                <div className="flex min-h-0 flex-col rounded-[10px] border p-3" style={{ background: "#12161C", borderColor: "#1F2630" }}>
                  <CandlestickChart series={charts.series.combined} />
                </div>
              )}
            </div>
          ) : (
            !chartsError && (
              <div className="flex min-h-0 flex-grow items-center justify-center text-sm text-[#8B96A5]">
                Loading live charts…
              </div>
            )
          )}

          {/* LEGS TABLE */}
          <div className="mt-3.5 flex-shrink-0 rounded-[10px] border p-3" style={{ background: "#12161C", borderColor: "#1F2630" }}>
            <div className="mb-2 text-[11.5px] font-semibold text-[#C7CDD6]">LEGS</div>
            <div className="flex flex-col gap-1.5">
              <div className="grid grid-cols-[0.6fr_0.7fr_1fr_1fr_1fr] gap-1.5 px-1 text-[9.5px] text-[#8B96A5]">
                <span>Action</span>
                <span>Strike</span>
                <span>Delta</span>
                <span>IV</span>
                <span>Premium</span>
              </div>
              {signal.legs.map((leg, i) => (
                <div key={i} className="grid grid-cols-[0.6fr_0.7fr_1fr_1fr_1fr] gap-1.5 rounded-[7px] p-2 text-xs" style={{ background: "#0E1116" }}>
                  <span className={leg.action === "SELL" ? "font-semibold text-[#F85149]" : "font-semibold text-[#3DD68C]"}>
                    {leg.action}
                  </span>
                  <span className="mono">
                    {leg.strike} {leg.option_type}
                  </span>
                  <span className="mono">{leg.delta.toFixed(2)}</span>
                  <span className="mono">{leg.iv.toFixed(1)}%</span>
                  <span className="mono text-[#8B96A5]">n/a</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
