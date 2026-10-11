"use client";

import { useMemo, useState } from "react";
import { exitAllLegs, exitPositionLeg, updatePositionLeg } from "@/lib/api";
import { formatCurrency, formatDateTime, formatStrategyLabel } from "@/lib/format";
import { formatRupees, legPnl, parsePrice } from "@/lib/validate";
import type { Position, PositionLeg } from "@/lib/types";

type BuySell = "all" | "BUY" | "SELL";

/** Full Positions & P&L grid — Phase 2 of the Inspiration gap-closing plan.
 * Per-leg SL/TSL/Target are alert thresholds and roll intentions the backend
 * position monitor watches; Exit / Exit All log a manually-executed fill,
 * they never place a broker order (read-only execution model). */
export default function PositionsPnlPanel({
  positions,
  onChanged,
}: {
  positions: Position[];
  onChanged: () => void;
}) {
  const [sideFilter, setSideFilter] = useState<BuySell>("all");
  const [expiryFilter, setExpiryFilter] = useState<string | "all">("all");
  const [maskPnl, setMaskPnl] = useState(false);
  const [closedOpen, setClosedOpen] = useState(true);
  const [busyLegId, setBusyLegId] = useState<number | null>(null);

  const open = useMemo(() => positions.filter((p) => p.status === "open"), [positions]);
  const closed = useMemo(() => positions.filter((p) => p.status === "closed"), [positions]);

  const expiries = useMemo(() => {
    const set = new Set(positions.map((p) => p.expiry));
    return [...set].sort();
  }, [positions]);

  const matchesFilters = (p: Position, l: PositionLeg) => {
    if (expiryFilter !== "all" && p.expiry !== expiryFilter) return false;
    if (sideFilter !== "all" && l.action !== sideFilter) return false;
    return true;
  };

  const openLegRows = useMemo(() => {
    const rows: { position: Position; leg: PositionLeg }[] = [];
    for (const p of open) {
      for (const l of p.legs) {
        if (l.closed_at === null && matchesFilters(p, l)) rows.push({ position: p, leg: l });
      }
    }
    return rows;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, sideFilter, expiryFilter]);

  const bookedPnl = positions.reduce((sum, p) => sum + p.booked_pnl, 0);
  const unbookedPnl = open.reduce((sum, p) => sum + p.unrealized_pnl, 0);
  const totalPnl = bookedPnl + unbookedPnl;

  const mask = (n: number) => (maskPnl ? "•••••" : formatCurrency(n));
  const pnlColor = (n: number) => (n >= 0 ? "text-[#3DD68C]" : "text-[#F85149]");

  async function handleSl(positionId: number, legId: number, value: string) {
    const num = value === "" ? null : parsePrice(value);
    if (value !== "" && num === null) return; // ignore invalid input instead of sending NaN
    try {
      setBusyLegId(legId);
      await updatePositionLeg(positionId, legId, { sl: num });
      onChanged();
    } finally {
      setBusyLegId(null);
    }
  }

  async function handleTarget(positionId: number, legId: number, value: string) {
    const num = value === "" ? null : parsePrice(value);
    if (value !== "" && num === null) return;
    try {
      setBusyLegId(legId);
      await updatePositionLeg(positionId, legId, { target: num });
      onChanged();
    } finally {
      setBusyLegId(null);
    }
  }

  async function handleExit(positionId: number, leg: PositionLeg) {
    const priceStr = window.prompt(`Log exit price for ${leg.strike} ${leg.option_type} (LTP ${leg.ltp})`, String(leg.ltp));
    if (priceStr === null) return;
    const price = parsePrice(priceStr);
    if (price === null) {
      window.alert("Enter a valid exit price (a non-negative number, e.g. 42.5).");
      return;
    }
    const pnl = legPnl(leg, price);
    if (!window.confirm(`Close ${leg.action} ${leg.strike} ${leg.option_type} x${leg.qty} at ${price}?\nRealised P&L: ${formatRupees(pnl)}\nThis only logs the exit; it does not place an order.`)) return;
    try {
      setBusyLegId(leg.id);
      await exitPositionLeg(positionId, leg.id, price);
      onChanged();
    } finally {
      setBusyLegId(null);
    }
  }

  async function handleExitAll(position: Position) {
    const priceStr = window.prompt(
      `Log the same exit price for all ${position.legs.filter((l) => !l.closed_at).length} open legs on this position:`,
      "",
    );
    if (priceStr === null) return;
    const price = parsePrice(priceStr);
    if (price === null) {
      window.alert("Enter a valid exit price (a non-negative number, e.g. 42.5).");
      return;
    }
    const openLegs = position.legs.filter((l) => !l.closed_at);
    const pnl = openLegs.reduce((sum, l) => sum + legPnl(l, price), 0);
    if (!window.confirm(`Close ${openLegs.length} legs at ${price}?\nRealised P&L: ${formatRupees(pnl)}\nThis only logs the exit; it does not place an order.`)) return;
    await exitAllLegs(position.id, price);
    onChanged();
  }

  return (
    <div className="flex flex-col gap-3">
      {/* Summary header */}
      <div className="flex flex-wrap items-center gap-5 rounded-[10px] border p-3.5" style={{ background: "#12161C", borderColor: "#1F2630" }}>
        <div>
          <div className="text-[10px] text-[#8B96A5]">Booked</div>
          <div className={`mono text-lg font-bold ${pnlColor(bookedPnl)}`}>{mask(bookedPnl)}</div>
        </div>
        <div>
          <div className="text-[10px] text-[#8B96A5]">Unbooked</div>
          <div className={`mono text-lg font-bold ${pnlColor(unbookedPnl)}`}>{mask(unbookedPnl)}</div>
        </div>
        <div>
          <div className="text-[10px] text-[#8B96A5]">Total P&amp;L</div>
          <div className={`mono text-lg font-bold ${pnlColor(totalPnl)}`}>{mask(totalPnl)}</div>
        </div>
        <div className="flex-grow" />
        <button
          onClick={() => setMaskPnl((v) => !v)}
          className="rounded-[7px] border border-[#232C36] px-2.5 py-1.5 text-xs text-[#8B96A5]"
          title="Toggle P&L visibility"
        >
          {maskPnl ? "Show P&L" : "Hide P&L"}
        </button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2.5 rounded-[10px] border p-2.5" style={{ background: "#12161C", borderColor: "#1F2630" }}>
        <span className="text-xs text-[#8B96A5]">Side</span>
        <div className="flex gap-0.5 rounded-[7px] bg-[#171C23] p-[3px]">
          {(["all", "BUY", "SELL"] as BuySell[]).map((s) => (
            <button
              key={s}
              onClick={() => setSideFilter(s)}
              className="rounded-[5px] px-3 py-1 text-xs"
              style={{
                background: sideFilter === s ? "#232C36" : "transparent",
                color: sideFilter === s ? "#E8EAED" : "#8B96A5",
                fontWeight: sideFilter === s ? 600 : 400,
              }}
            >
              {s === "all" ? "All" : s === "BUY" ? "Buy" : "Sell"}
            </button>
          ))}
        </div>
        <div className="mx-1 h-5 w-px bg-[#1F2630]" />
        <span className="text-xs text-[#8B96A5]">Expiry</span>
        <div className="flex flex-wrap gap-1.5">
          <button
            onClick={() => setExpiryFilter("all")}
            className="rounded-[7px] border px-2.5 py-1 text-xs"
            style={{
              background: expiryFilter === "all" ? "#232C36" : "#171C23",
              borderColor: expiryFilter === "all" ? "#2E3844" : "#1F2630",
            }}
          >
            All ({open.length})
          </button>
          {expiries.map((e) => {
            const count = open.filter((p) => p.expiry === e).length;
            if (count === 0) return null;
            return (
              <button
                key={e}
                onClick={() => setExpiryFilter(e)}
                className="rounded-[7px] border px-2.5 py-1 text-xs"
                style={{
                  background: expiryFilter === e ? "#232C36" : "#171C23",
                  borderColor: expiryFilter === e ? "#2E3844" : "#1F2630",
                }}
              >
                {new Date(e).toLocaleDateString("en-IN", { day: "2-digit", month: "short" })} ({count})
              </button>
            );
          })}
        </div>
      </div>

      {/* Open legs grid */}
      <div className="rounded-[10px] border p-3.5" style={{ background: "#12161C", borderColor: "#1F2630" }}>
        <div className="mb-2.5 flex items-center justify-between">
          <div className="text-xs font-semibold text-[#C7CDD6]">
            OPEN LEGS <span className="font-normal text-[#8B96A5]">· {openLegRows.length}</span>
          </div>
        </div>
        <div className="grid grid-cols-[1.6fr_0.6fr_0.7fr_0.7fr_0.8fr_0.8fr_0.8fr_0.9fr_0.6fr] gap-1.5 px-1 pb-1.5 text-[9.5px] text-[#8B96A5]">
          <span>Instrument</span>
          <span>Side</span>
          <span>Lots</span>
          <span>Avg</span>
          <span>LTP</span>
          <span>P&amp;L</span>
          <span>SL</span>
          <span>Target</span>
          <span>Exit</span>
        </div>
        <div className="flex flex-col gap-1">
          {openLegRows.length === 0 && (
            <div className="py-4 text-center text-xs text-[#8B96A5]">No open legs match the current filters.</div>
          )}
          {openLegRows.map(({ position, leg: l }) => (
            <div
              key={l.id}
              className="grid grid-cols-[1.6fr_0.6fr_0.7fr_0.7fr_0.8fr_0.8fr_0.8fr_0.9fr_0.6fr] items-center gap-1.5 rounded-[7px] px-1.5 py-1.5 text-xs"
              style={{ background: "#0E1116", opacity: busyLegId === l.id ? 0.5 : 1 }}
            >
              <span className="truncate">
                {position.symbol} {l.strike} {l.option_type}{" "}
                <span className="text-[#8B96A5]">{formatStrategyLabel(position.strategy_type)}</span>
              </span>
              <span className={l.action === "SELL" ? "font-semibold text-[#F85149]" : "font-semibold text-[#3DD68C]"}>
                {l.action}
              </span>
              <span className="mono">{l.qty}</span>
              <span className="mono">{l.entry_price.toFixed(2)}</span>
              <span className="mono">{l.ltp.toFixed(2)}</span>
              <span className={`mono font-bold ${pnlColor(l.unrealized_pnl)}`}>{mask(l.unrealized_pnl)}</span>
              <input
                type="number"
                defaultValue={l.sl ?? ""}
                onBlur={(e) => handleSl(position.id, l.id, e.target.value)}
                className="mono w-full rounded-[5px] border border-[#232C36] bg-[#0B0E11] px-1.5 py-1 text-[11px]"
                placeholder="—"
              />
              <input
                type="number"
                defaultValue={l.target ?? ""}
                onBlur={(e) => handleTarget(position.id, l.id, e.target.value)}
                className="mono w-full rounded-[5px] border border-[#232C36] bg-[#0B0E11] px-1.5 py-1 text-[11px]"
                placeholder="—"
              />
              <button
                onClick={() => handleExit(position.id, l)}
                className="rounded-[5px] border border-[#232C36] px-2 py-1 text-[10px] text-[#8B96A5]"
              >
                Exit
              </button>
            </div>
          ))}
        </div>
      </div>

      {/* Per-position Exit All */}
      {open.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {open.map((p) => (
            <button
              key={p.id}
              onClick={() => handleExitAll(p)}
              className="rounded-[7px] border border-[#3A1F22] bg-[#1F1013] px-3 py-1.5 text-xs text-[#F85149]"
            >
              Exit All · {p.symbol} {formatStrategyLabel(p.strategy_type)} {p.legs.map((l) => l.strike).join("/")}
            </button>
          ))}
        </div>
      )}

      {/* Closed today / closed positions */}
      <div className="rounded-[10px] border p-3.5" style={{ background: "#12161C", borderColor: "#1F2630" }}>
        <button
          onClick={() => setClosedOpen((v) => !v)}
          className="mb-2 flex w-full items-center justify-between text-xs font-semibold text-[#C7CDD6]"
        >
          <span>
            CLOSED POSITIONS <span className="font-normal text-[#8B96A5]">· {closed.length}</span>
          </span>
          <span className="text-[#8B96A5]">
            Booked P&amp;L: <span className={`mono font-bold ${pnlColor(bookedPnl)}`}>{mask(bookedPnl)}</span>{" "}
            {closedOpen ? "▾" : "▸"}
          </span>
        </button>
        {closedOpen && (
          <div className="flex flex-col gap-1.5">
            {closed.length === 0 && <div className="py-2 text-center text-xs text-[#8B96A5]">No closed positions yet.</div>}
            {closed.map((p) => (
              <div key={p.id} className="rounded-lg p-2.5 text-xs" style={{ background: "#0E1116" }}>
                <div className="flex items-center justify-between">
                  <span className="font-semibold">
                    {p.symbol} {formatStrategyLabel(p.strategy_type)} {p.legs.map((l) => l.strike).join("/")}
                  </span>
                  <span className={`mono font-bold ${pnlColor(p.booked_pnl)}`}>{mask(p.booked_pnl)}</span>
                </div>
                <div className="mt-1 text-[10px] text-[#8B96A5]">
                  {p.exit_time ? formatDateTime(p.exit_time) : ""} ·{" "}
                  {p.legs.map((l) => `${l.action} ${l.strike}${l.option_type} @ ${l.exit_price ?? "—"}`).join(" · ")}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
