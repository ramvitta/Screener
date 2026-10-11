"use client";

import Link from "next/link";
import type { MouseEvent } from "react";
import { daysToExpiry, formatCurrency, formatDate, formatPct, formatRatio, riskAdjustedReturn } from "@/lib/format";
import type { ScreenerSignal } from "@/lib/types";

// bull_put_spread/iron_condor/bear_call_spread are the only strategies the
// screener actually emits — straddle/strangle exist on StrategyType only for
// positions logged from the Chain & Entry tab, never as a screener signal —
// but the map covers all of StrategyType so indexing stays type-safe.
const STRATEGY_STYLES: Record<ScreenerSignal["strategy_type"], { accent: string; badgeBg: string; badgeFg: string; label: string }> = {
  bull_put_spread: { accent: "#3DD68C", badgeBg: "#173324", badgeFg: "#3DD68C", label: "BULL PUT SPREAD" },
  iron_condor: { accent: "#5B9BD5", badgeBg: "#152538", badgeFg: "#5B9BD5", label: "IRON CONDOR" },
  bear_call_spread: { accent: "#F0B429", badgeBg: "#241A12", badgeFg: "#F0B429", label: "BEAR CALL SPREAD" },
  straddle: { accent: "#8B96A5", badgeBg: "#1B2129", badgeFg: "#8B96A5", label: "STRADDLE" },
  strangle: { accent: "#8B96A5", badgeBg: "#1B2129", badgeFg: "#8B96A5", label: "STRANGLE" },
};

function LegChip({ leg }: { leg: ScreenerSignal["legs"][number] }) {
  return (
    <div
      className="flex-1 rounded-[7px] border px-2.5 py-2 text-xs"
      style={{ background: "#0E1116", borderColor: "#1A2028" }}
    >
      <span className={leg.action === "SELL" ? "font-semibold text-[#F85149]" : "font-semibold text-[#3DD68C]"}>
        {leg.action}
      </span>{" "}
      {leg.strike} {leg.option_type}{" "}
      <span className="mono text-[#8B96A5]">
        Δ {leg.delta.toFixed(2)} · IV {(leg.iv * 100).toFixed(1)}%
      </span>
    </div>
  );
}

async function logPosition(signal: ScreenerSignal, e: MouseEvent) {
  e.preventDefault();
  e.stopPropagation();
  const { createPosition } = await import("@/lib/api");
  try {
    await createPosition(signal);
  } catch {
    // Non-fatal for the dashboard shell — surfaced via the alerts feed once
    // the position-create endpoint round-trips in a later pass.
  }
}

export function SignalCardExpanded({ signal }: { signal: ScreenerSignal }) {
  const style = STRATEGY_STYLES[signal.strategy_type];
  const rar = riskAdjustedReturn(signal.risk_reward);

  return (
    <Link href={`/contracts/${signal.signal_id}`}>
      <div
        className="flex cursor-pointer flex-col gap-3 rounded-[10px] border p-4"
        style={{ background: "#12161C", borderColor: "#1F2630", borderLeft: `3px solid ${style.accent}` }}
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2.5">
            <span
              className="rounded-[5px] px-2 py-[3px] text-[11px] font-bold"
              style={{ background: style.badgeBg, color: style.badgeFg }}
            >
              {style.label}
            </span>
            <span className="rounded-[5px] bg-[#1B2129] px-1.5 py-[3px] text-[10px] font-bold text-[#8B96A5]">
              {signal.symbol}
            </span>
            <span className="text-[13px] font-semibold">
              {signal.legs.map((l) => l.strike).join("/")} {signal.legs[0]?.option_type}
            </span>
            <span className="text-[11px] text-[#8B96A5]">Exp {formatDate(signal.expiry)}</span>
            <span className="mono rounded-[5px] bg-[#0E1116] px-1.5 py-0.5 text-[11px] text-[#3DD68C]">
              DTE {daysToExpiry(signal.expiry)}
            </span>
          </div>
          <div className="flex items-center gap-3.5">
            <div className="text-right">
              <div className="text-[10px] text-[#8B96A5]">POP</div>
              <div className="mono text-[15px] font-bold text-[#3DD68C]">{formatPct(signal.risk_reward.pop)}</div>
            </div>
            <div className="text-right">
              <div className="text-[10px] text-[#8B96A5]">CREDIT / MAX LOSS</div>
              <div className="mono text-[15px] font-bold">
                {formatCurrency(signal.risk_reward.max_profit)} / {formatCurrency(signal.risk_reward.max_loss)}
              </div>
            </div>
            <div className="text-right">
              <div className="text-[10px] text-[#8B96A5]">RAR</div>
              <div className="mono text-[15px] font-bold text-[#F0B429]">{formatRatio(rar)}</div>
            </div>
          </div>
        </div>

        <div className="flex flex-wrap gap-2">
          {signal.legs.map((leg, i) => (
            <LegChip key={i} leg={leg} />
          ))}
        </div>

        <div className="rounded-[7px] bg-[#0E1116] px-3 py-2.5 text-xs leading-relaxed text-[#A7AFBA]">
          {signal.reason}
        </div>

        <div className="flex items-center gap-2.5">
          <div className="flex gap-4 text-[11px] text-[#8B96A5]">
            <span>
              Net Δ <span className="mono text-[#C7CDD6]">{signal.greeks.delta.toFixed(2)}</span>
            </span>
            <span>
              Θ <span className="mono text-[#3DD68C]">{signal.greeks.theta >= 0 ? "+" : ""}{signal.greeks.theta.toFixed(1)}</span>
            </span>
            <span>
              Vega <span className="mono text-[#C7CDD6]">{signal.greeks.vega.toFixed(1)}</span>
            </span>
            <span>
              Breakeven <span className="mono text-[#C7CDD6]">{signal.risk_reward.breakeven.toLocaleString("en-IN")}</span>
            </span>
          </div>
          <div className="flex-grow" />
          <span className="rounded-[7px] border border-[#232C36] px-3.5 py-1.5 text-xs text-[#8B96A5]">
            View Charts
          </span>
          <button
            onClick={(e) => logPosition(signal, e)}
            className="rounded-[7px] bg-[#1F8F5C] px-3.5 py-1.5 text-xs font-semibold text-white"
          >
            Log Position
          </button>
        </div>
      </div>
    </Link>
  );
}

export function SignalCardCompact({ signal }: { signal: ScreenerSignal }) {
  const style = STRATEGY_STYLES[signal.strategy_type];
  const rar = riskAdjustedReturn(signal.risk_reward);

  return (
    <Link href={`/contracts/${signal.signal_id}`}>
      <div
        className="flex cursor-pointer items-center justify-between rounded-[10px] border px-4 py-3.5 opacity-85"
        style={{ background: "#12161C", borderColor: "#1F2630", borderLeft: "3px solid #8B96A5" }}
      >
        <div className="flex items-center gap-2.5">
          <span
            className="rounded-[5px] px-2 py-[3px] text-[11px] font-bold"
            style={{ background: style.badgeBg, color: style.badgeFg }}
          >
            {style.label}
          </span>
          <span className="rounded-[5px] bg-[#1B2129] px-1.5 py-[3px] text-[10px] font-bold text-[#8B96A5]">
            {signal.symbol}
          </span>
          <span className="text-[13px] font-semibold">
            {signal.legs.map((l) => l.strike).join("/")} {signal.legs[0]?.option_type}
          </span>
          <span className="text-[11px] text-[#8B96A5]">Exp {formatDate(signal.expiry)}</span>
          <span className="mono rounded-[5px] bg-[#0E1116] px-1.5 py-0.5 text-[11px] text-[#F0B429]">
            DTE {daysToExpiry(signal.expiry)}
          </span>
        </div>
        <div className="flex items-center gap-4.5 text-xs">
          <span>
            POP <span className="mono font-bold text-[#3DD68C]">{formatPct(signal.risk_reward.pop)}</span>
          </span>
          <span>
            Credit/Loss{" "}
            <span className="mono font-bold">
              {formatCurrency(signal.risk_reward.max_profit)}/{formatCurrency(signal.risk_reward.max_loss)}
            </span>
          </span>
          <span>
            RAR <span className="mono font-bold text-[#F0B429]">{formatRatio(rar)}</span>
          </span>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#8B96A5" strokeWidth="2">
            <path d="M6 9l6 6 6-6" />
          </svg>
        </div>
      </div>
    </Link>
  );
}
