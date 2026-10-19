"use client";

import { useEffect, useState } from "react";
import { STRATEGY_FAMILIES } from "@/lib/strategies";
import { fetchSignalHistory, fetchSignalHistoryDetail } from "@/lib/api";
import AppHeader from "./AppHeader";
import { SignalRow } from "./SignalHistoryRow";
import type { SignalHistoryDetail, SignalHistoryItem } from "@/lib/types";

/** Per-strategy view: every stored screener signal (accepted + rejected) for
 * the strategy family, read from the screener_signals table via
 * GET /api/v1/screener/history. A family may aggregate several concrete
 * `StrategyType`s (e.g. Income Spreads = bull put + bear call + iron condor),
 * so we fetch each and merge by recency. */
export default function StrategySignalsPage({ familyKey }: { familyKey: string }) {
  const family = STRATEGY_FAMILIES.find((f) => f.key === familyKey);

  const hasTypes = (family?.strategyTypes.length ?? 0) > 0;
  const [signals, setSignals] = useState<SignalHistoryItem[]>([]);
  const [details, setDetails] = useState<Record<number, SignalHistoryDetail>>({});
  const [loading, setLoading] = useState(hasTypes);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!family || family.strategyTypes.length === 0) return;
    let cancelled = false;
    Promise.all(family.strategyTypes.map((t) => fetchSignalHistory(500, 0, { strategy_type: t })))
      .then((results) => {
        if (cancelled) return;
        setSignals(results.flat().sort((a, b) => b.id - a.id));
        setLoading(false);
      })
      .catch(() => {
        if (!cancelled) {
          setError("Could not load signals — is the backend running?");
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [family]);

  const toggleDetail = (id: number) => {
    setDetails((prev) => {
      if (id in prev) return prev;
      fetchSignalHistoryDetail(id).then((d) => setDetails((p) => ({ ...p, [id]: d }))).catch(() => {});
      return prev;
    });
  };

  const header = <AppHeader />;

  if (!family) {
    return (
      <div className="flex h-screen flex-col bg-[#0B0E11] text-[#E8EAED]">
        {header}
        <div className="flex flex-grow items-center justify-center text-sm text-[#8B96A5]">
          Unknown strategy “{familyKey}”.
        </div>
      </div>
    );
  }

  const live = family.status === "live";
  const accepted = signals.filter((s) => s.status === "accepted");
  const rejected = signals.filter((s) => s.status === "rejected");
  const latest = signals.length ? signals[0].generated_at : null;

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-[#0B0E11] text-[#E8EAED]">
      {header}

      <div className="min-h-0 flex-grow overflow-y-auto px-6 py-5">
        <div className="mx-auto flex max-w-5xl flex-col gap-4">
          {/* Title block */}
          <div
            className="rounded-[10px] border p-4"
            style={{ background: "#12161C", borderColor: "#1F2630", borderLeft: `3px solid ${family.accent}` }}
          >
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="flex items-center gap-2.5">
                  <h1 className="text-xl font-semibold">{family.label}</h1>
                  <span
                    className="rounded-[5px] px-2 py-[3px] text-[10px] font-bold"
                    style={{
                      background: live ? "#173324" : "#1B2129",
                      color: live ? "#3DD68C" : "#8B96A5",
                    }}
                  >
                    {live ? "LIVE" : "PLANNED"}
                  </span>
                </div>
                <p className="mt-1 text-sm text-[#8B96A5]">{family.description}</p>
              </div>
              <div className="flex items-center gap-5 text-right">
                <div>
                  <div className="text-[10px] text-[#8B96A5]">TOTAL</div>
                  <div className="mono text-lg font-bold">{signals.length}</div>
                </div>
                <div>
                  <div className="text-[10px] text-[#8B96A5]">ACCEPTED</div>
                  <div className="mono text-lg font-bold text-[#3DD68C]">{accepted.length}</div>
                </div>
                <div>
                  <div className="text-[10px] text-[#8B96A5]">REJECTED</div>
                  <div className="mono text-lg font-bold text-[#F85149]">{rejected.length}</div>
                </div>
              </div>
            </div>
            {latest && (
              <div className="mt-2 text-[11px] text-[#8B96A5]">
                Latest signal generated {new Date(latest).toLocaleString("en-IN")}
              </div>
            )}
          </div>

          {error && <p className="text-sm text-[#F85149]">{error}</p>}

          {loading ? (
            <p className="text-sm text-[#8B96A5]">Loading…</p>
          ) : signals.length === 0 ? (
            <div
              className="rounded-[10px] border px-4 py-10 text-center text-sm text-[#8B96A5]"
              style={{ background: "#12161C", borderColor: "#1F2630" }}
            >
              {live
                ? "No signals stored for this strategy yet."
                : "This strategy is not yet wired into the screener engine — no signals are stored for it. See the backend integration phase."}
            </div>
          ) : (
            <div className="flex flex-col gap-2">
              {signals.map((s) => {
                const d = details[s.id];
                const row: SignalHistoryDetail = d ?? { ...s, signal_json: null, decision_json: null };
                return (
                  <div key={s.id} onClick={() => toggleDetail(s.id)}>
                    <SignalRow signal={row} />
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
