"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { fetchSignalHistory, fetchSignalHistoryDetail, fetchScreenerRun, fetchScreenerRuns } from "@/lib/api";
import { formatCurrency, formatDateTime, formatPct, formatStrategyLabel } from "@/lib/format";
import type {
  ScreenerRun,
  ScreenerRunDetail,
  SignalDecisionComputed,
  SignalHistoryDetail,
  SignalHistoryItem,
  StrategyType,
} from "@/lib/types";

const STRATEGIES: StrategyType[] = ["bull_put_spread", "bear_call_spread", "iron_condor"];

function rejectLabel(reason: string | null): string {
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

function StatusBadge({ status }: { status: "accepted" | "rejected" }) {
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

function decisionMetrics(c: SignalDecisionComputed | null | undefined): { label: string; value: string }[] {
  if (!c) return [];
  const num = (v: number | null | undefined, fmt: (n: number) => string) => (v === null || v === undefined ? "—" : fmt(v));
  return [
    { label: "IV Rank", value: num(c.iv_rank, (n) => `${n.toFixed(0)}%`) },
    { label: "POP", value: num(c.pop, formatPct) },
    { label: "Credit", value: num(c.credit, formatCurrency) },
    { label: "Max loss", value: num(c.max_loss, formatCurrency) },
    { label: "RSI", value: num(c.rsi, (n) => n.toFixed(0)) },
    { label: "Breakeven", value: num(c.breakeven, (n) => n.toFixed(0)) },
  ];
}

function SignalRow({ signal }: { signal: SignalHistoryDetail }) {
  const [open, setOpen] = useState(false);
  const metrics = decisionMetrics(signal.decision_json?.computed);
  return (
    <div className="rounded-[8px] border border-[#1F2630] bg-[#1B2129]">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-3 px-3 py-2 text-left"
      >
        <span className="text-[10px] text-[#5B6675]">{open ? "▾" : "▸"}</span>
        <StatusBadge status={signal.status} />
        <span className="text-[13px] font-medium text-[#C7CDD6]">
          {signal.strategy_type ? formatStrategyLabel(signal.strategy_type) : "—"}
        </span>
        <span className="text-xs text-[#8B96A5]">{signal.symbol ?? ""}</span>
        {signal.status === "rejected" && (
          <span className="text-xs text-[#F85149]">{rejectLabel(signal.reject_reason)}</span>
        )}
        <span className="flex-grow" />
        {signal.status === "accepted" && signal.signal_id && (
          <Link
            href={`/contracts/${signal.signal_id}`}
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

function RunCard({ run, onToggle, detail }: { run: ScreenerRun; onToggle: () => void; detail?: ScreenerRunDetail | null }) {
  const [open, setOpen] = useState(false);
  const statusColor = run.status === "degraded" ? "#F85149" : run.status === "sample" ? "#E6B800" : "#3DD68C";
  return (
    <div className="rounded-[8px] border border-[#1F2630] bg-[#12161C]">
      <button onClick={() => { setOpen((o) => !o); onToggle(); }} className="flex w-full items-center gap-3 px-3 py-2.5 text-left">
        <span className="text-[10px] text-[#5B6675]">{open ? "▾" : "▸"}</span>
        <span className="mono text-xs text-[#C7CDD6]">{formatDateTime(run.started_at)}</span>
        <span
          className="rounded-[5px] px-2 py-0.5 text-[10px] font-semibold uppercase"
          style={{ color: statusColor, background: `${statusColor}1A`, border: `1px solid ${statusColor}55` }}
        >
          {run.status}
        </span>
        <span className="text-xs text-[#8B96A5]">{run.data_source ?? "—"}</span>
        <span className="text-xs text-[#8B96A5]">
          {run.signal_count} signal{run.signal_count === 1 ? "" : "s"}
        </span>
        {run.message && <span className="truncate text-xs text-[#5B6675]">{run.message}</span>}
      </button>
      {open && (
        <div className="border-t border-[#1F2630] p-3">
          {detail === undefined ? (
            <span className="text-xs text-[#5B6675]">Loading…</span>
          ) : detail === null ? (
            <span className="text-xs text-[#F85149]">Failed to load run detail.</span>
          ) : detail.signals.length === 0 ? (
            <span className="text-xs text-[#8B96A5]">No signals this run.</span>
          ) : (
            <div className="flex flex-col gap-2">
              {detail.signals.map((s) => <SignalRow key={s.id} signal={s} />)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function SignalHistoryPage() {
  const [tab, setTab] = useState<"runs" | "signals">("runs");
  const [runs, setRuns] = useState<ScreenerRun[]>([]);
  const [details, setDetails] = useState<Record<string, ScreenerRunDetail | null>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // signals tab state
  const [signals, setSignals] = useState<SignalHistoryItem[]>([]);
  const [signalDetails, setSignalDetails] = useState<Record<number, SignalHistoryDetail>>({});
  const [statusFilter, setStatusFilter] = useState("");
  const [strategyFilter, setStrategyFilter] = useState("");
  const [symbolFilter, setSymbolFilter] = useState("");

  useEffect(() => {
    fetchScreenerRuns(50)
      .then(setRuns)
      .catch(() => setError("Could not load signal history — is the backend running?"))
      .finally(() => setLoading(false));
  }, []);

  const loadSignals = useCallback(() => {
    fetchSignalHistory(200, 0, {
      status: statusFilter || undefined,
      strategy_type: strategyFilter || undefined,
      symbol: symbolFilter || undefined,
    })
      .then(setSignals)
      .catch(() => setError("Could not load signals — is the backend running?"));
  }, [statusFilter, strategyFilter, symbolFilter]);

  useEffect(() => {
    if (tab === "signals") loadSignals();
  }, [tab, loadSignals]);

  const toggleRun = useCallback(
    (runId: string) => {
      setDetails((prev) => {
        if (runId in prev) return prev;
        fetchScreenerRun(runId)
          .then((d) => setDetails((p) => ({ ...p, [runId]: d })))
          .catch(() => setDetails((p) => ({ ...p, [runId]: null })));
        return prev;
      });
    },
    [],
  );

  const toggleSignal = useCallback((id: number) => {
    setSignalDetails((prev) => {
      if (id in prev) return prev;
      fetchSignalHistoryDetail(id).then((d) => setSignalDetails((p) => ({ ...p, [id]: d }))).catch(() => {});
      return prev;
    });
  }, []);

  return (
    <div className="mx-auto max-w-5xl px-6 py-6">
      <div className="mb-5 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-[#C7CDD6]">Signal History</h1>
          <p className="mt-1 text-xs text-[#8B96A5]">
            Every screener run — accepted signals and rejected candidates, with the decision inputs behind each.
          </p>
        </div>
        <div className="flex items-center gap-1 rounded-[8px] border border-[#1F2630] bg-[#12161C] p-1">
          {(["runs", "signals"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className="rounded-[6px] px-3 py-1 text-xs font-medium capitalize"
              style={{ background: tab === t ? "#1B2129" : "transparent", color: tab === t ? "#C7CDD6" : "#8B96A5" }}
            >
              {t}
            </button>
          ))}
        </div>
      </div>

      {tab === "signals" && (
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} className="rounded-[6px] border border-[#1F2630] bg-[#12161C] px-2 py-1.5 text-xs text-[#C7CDD6]">
            <option value="">All statuses</option>
            <option value="accepted">Accepted</option>
            <option value="rejected">Rejected</option>
          </select>
          <select value={strategyFilter} onChange={(e) => setStrategyFilter(e.target.value)} className="rounded-[6px] border border-[#1F2630] bg-[#12161C] px-2 py-1.5 text-xs text-[#C7CDD6]">
            <option value="">All strategies</option>
            {STRATEGIES.map((s) => <option key={s} value={s}>{formatStrategyLabel(s)}</option>)}
          </select>
          <input
            value={symbolFilter}
            onChange={(e) => setSymbolFilter(e.target.value.toUpperCase())}
            placeholder="Symbol (e.g. NIFTY)"
            className="rounded-[6px] border border-[#1F2630] bg-[#12161C] px-2 py-1.5 text-xs text-[#C7CDD6] placeholder:text-[#5B6675]"
          />
        </div>
      )}

      {error && <p className="mb-4 text-sm text-[#F85149]">{error}</p>}
      {loading && tab === "runs" && <p className="text-sm text-[#8B96A5]">Loading…</p>}

      {tab === "runs" && !loading && (
        <div className="flex flex-col gap-2">
          {runs.length === 0 && <p className="text-sm text-[#8B96A5]">No screener runs recorded yet.</p>}
          {runs.map((r) => (
            <RunCard key={r.run_id} run={r} detail={details[r.run_id]} onToggle={() => toggleRun(r.run_id)} />
          ))}
        </div>
      )}

      {tab === "signals" && (
        <div className="flex flex-col gap-2">
          {signals.length === 0 && <p className="text-sm text-[#8B96A5]">No signals match the current filters.</p>}
          {signals.map((s) => {
            const d = signalDetails[s.id];
            const row: SignalHistoryDetail = d ?? {
              ...s,
              signal_json: null,
              decision_json: null,
            };
            return (
              <div key={s.id} onClick={() => toggleSignal(s.id)}>
                <SignalRow signal={row} />
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
