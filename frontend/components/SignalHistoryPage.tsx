"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { fetchSignalHistory, fetchSignalHistoryDetail, fetchScreenerRun, fetchScreenerRuns } from "@/lib/api";
import { formatDateTime, formatStrategyLabel } from "@/lib/format";
import { SignalRow } from "./SignalHistoryRow";
import type {
  ScreenerRun,
  ScreenerRunDetail,
  SignalHistoryDetail,
  SignalHistoryItem,
  StrategyType,
} from "@/lib/types";

const STRATEGIES: StrategyType[] = ["bull_put_spread", "bear_call_spread", "iron_condor"];

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
  // The worker stores one row per run, so the unfiltered signal list repeats
  // any symbol that keeps qualifying. Default to the newest row per
  // strategy/symbol; the run cards remain the full historical record.
  const [latestOnly, setLatestOnly] = useState(true);

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
      latest_only: latestOnly,
    })
      .then(setSignals)
      .catch(() => setError("Could not load signals — is the backend running?"));
  }, [statusFilter, strategyFilter, symbolFilter, latestOnly]);

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
    <div className="px-6 py-6">
      <Link href="/" className="mb-3 inline-flex items-center gap-1 rounded-[6px] border px-2 py-1 text-[11px] font-semibold text-[#8B96A5] hover:text-[#C7CDD6]" style={{ borderColor: "#232C36" }}>
        ← Dashboard
      </Link>
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
          <label className="flex items-center gap-1.5 text-xs text-[#8B96A5]">
            <input
              type="checkbox"
              checked={latestOnly}
              onChange={(e) => setLatestOnly(e.target.checked)}
              className="accent-[#3DD68C]"
            />
            Latest per symbol
          </label>
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
