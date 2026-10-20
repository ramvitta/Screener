"use client";

import Link from "next/link";
import { useState } from "react";
import { formatDateTime, formatStrategyLabel } from "@/lib/format";
import { decisionMetrics, tierStyle } from "@/lib/signalMetrics";
import type { SignalHistoryDetail } from "@/lib/types";

export function rejectLabel(reason: string | null): string {
  switch (reason) {
    case "no_short_strike":
      return "No short strike found";
    case "no_long_strike":
      return "No long (protection) strike found";
    case "iv_rank_below_min":
      return "IV rank below minimum";
    case "pop_below_min":
      return "POP below minimum";
    case "credit_non_positive":
      return "Non-positive credit";
    case "invariant_failed":
      return "Failed invariant guardrail";
    case "wing_missing":
      return "Missing wing (iron condor)";
    default:
      return reason ?? "Rejected";
  }
}

export function StatusBadge({ status }: { status: "accepted" | "rejected" }) {
  const color = status === "accepted" ? "#3DD68C" : "#F85149";
  return (
    <span
      className="rounded-[5px] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide"
      style={{ color, background: `${color}1A`, border: `1px solid ${color}55` }}
    >
      {status}
    </span>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5 rounded-[6px] bg-[#12161C] px-2.5 py-1.5">
      <span className="text-[10px] text-[#8B96A5]">{label}</span>
      <span className="mono text-[12px] font-semibold text-[#C7CDD6]">{value}</span>
    </div>
  );
}

/** A single stored screener signal (accepted or rejected) with expandable
 * decision inputs. Used by both the global Signal History page and the
 * per-strategy pages. */
export function SignalRow({ signal }: { signal: SignalHistoryDetail }) {
  const [open, setOpen] = useState(false);
  const metrics = decisionMetrics(signal);
  const tier = tierStyle(signal.tier);
  return (
    <div
      className="rounded-[8px] border border-[#1F2630] bg-[#1B2129]"
      style={tier ? { borderLeft: `3px solid ${tier.color}` } : undefined}
    >
      <button onClick={() => setOpen((o) => !o)} className="flex w-full items-center gap-3 px-3 py-2 text-left">
        <span className="text-[10px] text-[#5B6675]">{open ? "▾" : "▸"}</span>
        <StatusBadge status={signal.status} />
        {tier && (
          <span
            className="rounded-[5px] px-2 py-0.5 text-[10px] font-bold"
            style={{ color: tier.color, background: tier.bg, border: `1px solid ${tier.border}` }}
          >
            {tier.label}
          </span>
        )}
        <span className="text-[13px] font-medium text-[#C7CDD6]">
          {signal.strategy_type ? formatStrategyLabel(signal.strategy_type) : "—"}
        </span>
        <span className="text-xs text-[#8B96A5]">{signal.symbol ?? ""}</span>
        {signal.status === "rejected" && (
          <span className="text-xs text-[#F85149]">{rejectLabel(signal.reject_reason)}</span>
        )}
        <span className="flex-grow" />
        {signal.status === "accepted" && (
          <Link
            href={`/signals/${signal.id}`}
            onClick={(e) => e.stopPropagation()}
            className="rounded-[5px] border border-[#232C36] px-2 py-0.5 text-[11px] text-[#3DD68C]"
          >
            View →
          </Link>
        )}
      </button>
      {open && (
        <div className="border-t border-[#1F2630] px-3 py-3">
          {signal.status === "accepted" && signal.reason && (
            <p className="mb-3 text-xs leading-relaxed text-[#8B96A5]">{signal.reason}</p>
          )}
          {signal.status === "rejected" && signal.decision_json?.computed?.detail && (
            <p className="mb-3 text-xs text-[#8B96A5]">{signal.decision_json.computed.detail}</p>
          )}
          {metrics.length > 0 && (
            <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">{metrics.map((m) => <Metric key={m.label} {...m} />)}</div>
          )}
          <p className="mt-2 text-[10px] text-[#5B6675]">
            Generated {signal.generated_at ? formatDateTime(signal.generated_at) : "—"} · {signal.data_source ?? "unknown"}
            {signal.reason_source === "llm" ? " · LLM reason" : ""}
          </p>
        </div>
      )}
    </div>
  );
}
