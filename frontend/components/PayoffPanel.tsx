"use client";

import { useEffect, useMemo, useState } from "react";
import PayoffChart from "./charts/PayoffChart";
import { fetchPayoff } from "@/lib/api";
import { daysToExpiry as computeDaysToExpiry, formatStrategyLabel } from "@/lib/format";
import type { Position, PayoffLeg, PayoffResponse, ScreenerSignal } from "@/lib/types";

interface Combo {
  key: string;
  label: string;
  symbol: string;
  expiry: string;
  legs: PayoffLeg[];
}

function combosFromSignals(signals: ScreenerSignal[]): Combo[] {
  return signals.map((s) => ({
    key: `signal:${s.signal_id}`,
    label: `${s.symbol} ${formatStrategyLabel(s.strategy_type)} ${s.legs.map((l) => l.strike).join("/")} (signal)`,
    symbol: s.symbol,
    expiry: s.expiry,
    // No real fill yet — entry_price omitted so the backend prices each leg
    // at today's live theoretical premium ("what if I entered this now").
    legs: s.legs.map((l) => ({ strike: l.strike, option_type: l.option_type, action: l.action, qty: 1, iv: l.iv })),
  }));
}

function combosFromPositions(positions: Position[]): Combo[] {
  return positions
    .filter((p) => p.status === "open")
    .map((p) => ({
      key: `position:${p.id}`,
      label: `${p.symbol} ${formatStrategyLabel(p.strategy_type)} ${p.legs.map((l) => l.strike).join("/")} (open position)`,
      symbol: p.symbol,
      expiry: p.expiry,
      legs: p.legs
        .filter((l) => !l.closed_at)
        .map((l) => ({
          strike: l.strike,
          option_type: l.option_type,
          action: l.action,
          qty: l.qty,
          entry_price: l.entry_price,
          iv: 0,
          current_price: l.ltp,
        })),
    }));
}

/** Analytics tab's payoff view — Phase 3 of the UI gap-closing plan. Picks
 * from live screener signals or open positions, with a target-date slider
 * that re-fetches the on-target-date curve from the backend. */
export default function PayoffPanel({ signals, positions }: { signals: ScreenerSignal[]; positions: Position[] }) {
  const combos = useMemo(() => [...combosFromPositions(positions), ...combosFromSignals(signals)], [signals, positions]);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [targetDays, setTargetDays] = useState(0);
  const [payoff, setPayoff] = useState<PayoffResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const selected = combos.find((c) => c.key === selectedKey) ?? combos[0] ?? null;

  const daysToExpiry = selected ? computeDaysToExpiry(selected.expiry) : 0;
  // Derived, not stored: if the stale `targetDays` from a previous (longer-
  // dated) combo overshoots the newly selected combo's expiry, clamp it for
  // display/fetch without needing an effect to write it back into state.
  const effectiveTargetDays = Math.min(targetDays, daysToExpiry);

  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    const targetDate = new Date(Date.now() + effectiveTargetDays * 86_400_000).toISOString().slice(0, 10);
    fetchPayoff(selected.symbol, selected.expiry, selected.legs, targetDate)
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
  }, [selected, effectiveTargetDays]);

  if (combos.length === 0) {
    return (
      <div className="flex min-h-0 flex-grow items-center justify-center text-sm text-[#8B96A5]">
        No live signals or open positions yet to chart a payoff for.
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-grow flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3 rounded-[10px] border p-3" style={{ background: "#12161C", borderColor: "#1F2630" }}>
        <span className="text-xs text-[#8B96A5]">Combo</span>
        <select
          value={selected?.key ?? ""}
          onChange={(e) => setSelectedKey(e.target.value)}
          className="rounded-[7px] border border-[#232C36] bg-[#0E1116] px-2.5 py-1.5 text-xs text-[#E8EAED]"
        >
          {combos.map((c) => (
            <option key={c.key} value={c.key}>
              {c.label}
            </option>
          ))}
        </select>
        <div className="flex flex-grow items-center gap-2.5">
          <span className="text-xs text-[#8B96A5]">Target date</span>
          <input
            type="range"
            min={0}
            max={Math.max(daysToExpiry, 1)}
            value={effectiveTargetDays}
            onChange={(e) => setTargetDays(Number(e.target.value))}
            className="flex-grow"
          />
          <span className="mono text-xs text-[#C7CDD6]">
            {effectiveTargetDays === 0 ? "Today" : `+${effectiveTargetDays}d`} ·{" "}
            {effectiveTargetDays === daysToExpiry ? "expiry" : `${daysToExpiry - effectiveTargetDays}d to expiry`}
          </span>
        </div>
      </div>

      {error && (
        <div className="rounded-[8px] border border-amber-900 bg-amber-950/60 px-3 py-2 text-xs text-amber-300">{error}</div>
      )}

      <div className="flex min-h-0 flex-grow flex-col rounded-[10px] border p-3.5" style={{ background: "#12161C", borderColor: "#1F2630" }}>
        {payoff ? <PayoffChart payoff={payoff} /> : !error && <div className="flex min-h-0 flex-1 items-center justify-center text-sm text-[#8B96A5]">Loading payoff…</div>}
      </div>
    </div>
  );
}
