"use client";

import { useEffect, useState } from "react";
import PayoffChart from "./charts/PayoffChart";
import { fetchPayoff } from "@/lib/api";
import { daysToExpiry } from "@/lib/format";
import type { PayoffResponse, ScreenerSignal } from "@/lib/types";

/** Payoff section for a single screener signal on the contract-detail page.
 * Reuses the existing `PayoffChart` + `fetchPayoff` (Phase 3) — the signal's
 * legs are priced at today's live theoretical premium ("what if I entered
 * this now"), with a target-date slider that re-fetches the curve. */
export default function SignalPayoffPanel({ signal }: { signal: ScreenerSignal }) {
  const [targetDays, setTargetDays] = useState(0);
  const [payoff, setPayoff] = useState<PayoffResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const dte = daysToExpiry(signal.expiry);
  // Clamp a stale slider value when the selected signal's expiry is shorter.
  const effectiveTargetDays = Math.min(targetDays, dte);

  useEffect(() => {
    let cancelled = false;
    const legs = signal.legs.map((l) => ({ strike: l.strike, option_type: l.option_type, action: l.action, qty: 1, iv: l.iv }));
    const targetDate = new Date(Date.now() + effectiveTargetDays * 86_400_000).toISOString().slice(0, 10);
    fetchPayoff(signal.symbol, signal.expiry, legs, targetDate)
      .then((p) => {
        if (!cancelled) {
          setPayoff(p);
          setError(null);
        }
      })
      .catch(() => {
        if (!cancelled) setError("Could not load payoff — is the backend logged in to Definedge?");
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signal.signal_id, signal.expiry, effectiveTargetDays]);

  return (
    <div className="flex h-full flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3 rounded-[10px] border p-3" style={{ background: "#12161C", borderColor: "#1F2630" }}>
        <span className="text-[11.5px] font-semibold text-[#C7CDD6]">PAYOFF</span>
        <div className="flex flex-grow items-center gap-2.5">
          <span className="text-xs text-[#8B96A5]">Target date</span>
          <input
            type="range"
            min={0}
            max={Math.max(dte, 1)}
            value={effectiveTargetDays}
            onChange={(e) => setTargetDays(Number(e.target.value))}
            className="flex-grow"
          />
          <span className="mono text-xs text-[#C7CDD6]">
            {effectiveTargetDays === 0 ? "Today" : `+${effectiveTargetDays}d`} ·{" "}
            {effectiveTargetDays === dte ? "expiry" : `${dte - effectiveTargetDays}d to expiry`}
          </span>
        </div>
      </div>

      {error && <div className="rounded-[8px] border border-amber-900 bg-amber-950/60 px-3 py-2 text-xs text-amber-300">{error}</div>}

      <div className="flex min-h-0 flex-grow flex-col rounded-[10px] border p-3.5" style={{ background: "#12161C", borderColor: "#1F2630" }}>
        {payoff ? (
          <PayoffChart payoff={payoff} />
        ) : (
          !error && <div className="flex min-h-0 flex-1 items-center justify-center text-sm text-[#8B96A5]">Loading payoff…</div>
        )}
      </div>
    </div>
  );
}
