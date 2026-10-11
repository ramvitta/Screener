"use client";

import { useEffect, useState } from "react";
import { createPositionFromLegs, fetchOptionChain } from "@/lib/api";
import { formatCurrency } from "@/lib/format";
import type { ChainResponse } from "@/lib/types";

const CHAIN_POLL_MS = 5000;
const DEFAULT_LOT_QTY = 1;

type Mode = "straddle" | "strangle";

/** Chain & Entry tab — Phase 4 of the UI gap-closing plan. Read-only chain
 * display; "Log" buttons record a position (POST /api/v1/positions) rather
 * than placing a broker order, per the read-only execution model. */
export default function OptionChainPanel() {
  const [mode, setMode] = useState<Mode>("straddle");
  const [depth, setDepth] = useState(5);
  const [chain, setChain] = useState<ChainResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function poll() {
      try {
        const c = await fetchOptionChain("NIFTY", depth);
        if (!cancelled) {
          setChain(c);
          setError(null);
        }
      } catch {
        if (!cancelled) setError("Could not load the option chain — is the backend logged in to Definedge?");
      }
    }
    poll();
    const interval = setInterval(poll, CHAIN_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [depth]);

  async function logCombo(otmDepth: number) {
    if (!chain) return;
    const row = chain.rows.find((r) => r.otm === otmDepth);
    if (!row || !row.ce || !row.pe) return;
    setBusy(true);
    setMessage(null);
    try {
      await createPositionFromLegs(otmDepth === 0 ? "straddle" : "strangle", chain.symbol, chain.expiry, [
        { strike: row.ce.strike, option_type: "CE", action: "SELL", qty: DEFAULT_LOT_QTY, entry_price: row.ce.ltp },
        { strike: row.pe.strike, option_type: "PE", action: "SELL", qty: DEFAULT_LOT_QTY, entry_price: row.pe.ltp },
      ]);
      setMessage(
        otmDepth === 0
          ? `Logged ATM straddle at ${row.strike} (CE ${row.ce.ltp} + PE ${row.pe.ltp})`
          : `Logged strangle ${row.strike} CE / ${row.strike} PE`,
      );
    } catch {
      setMessage("Could not log the position — see console/backend logs.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-0 flex-grow flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3 rounded-[10px] border p-3" style={{ background: "#12161C", borderColor: "#1F2630" }}>
        <span className="text-xs text-[#8B96A5]">Mode</span>
        <div className="flex gap-0.5 rounded-[7px] bg-[#171C23] p-[3px]">
          {(["straddle", "strangle"] as Mode[]).map((m) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className="rounded-[5px] px-3 py-1.5 text-xs capitalize"
              style={{
                background: mode === m ? "#232C36" : "transparent",
                color: mode === m ? "#E8EAED" : "#8B96A5",
                fontWeight: mode === m ? 600 : 400,
              }}
            >
              {m}
            </button>
          ))}
        </div>
        <span className="text-xs text-[#8B96A5]">OTM depth</span>
        <input
          type="range"
          min={1}
          max={10}
          value={depth}
          onChange={(e) => setDepth(Number(e.target.value))}
          className="w-28"
        />
        <span className="mono text-xs text-[#C7CDD6]">±{depth}</span>
        <div className="flex-grow" />
        {chain && (
          <span className="text-[11px] text-[#8B96A5]">
            {chain.symbol} · Exp {new Date(chain.expiry).toLocaleDateString("en-IN", { day: "2-digit", month: "short" })} · Spot{" "}
            <span className="mono text-[#C7CDD6]">{chain.spot.toLocaleString("en-IN")}</span> · updated{" "}
            {new Date(chain.updated_at).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
          </span>
        )}
        <button
          disabled={!chain || busy}
          onClick={() => logCombo(mode === "straddle" ? 0 : depth)}
          className="rounded-[7px] bg-[#1F8F5C] px-3.5 py-1.5 text-xs font-semibold text-white disabled:opacity-50"
        >
          {mode === "straddle" ? "Log ATM Straddle" : `Log Strangle ±${depth * 50}`}
        </button>
      </div>

      {error && <div className="rounded-[8px] border border-amber-900 bg-amber-950/60 px-3 py-2 text-xs text-amber-300">{error}</div>}
      {message && <div className="rounded-[8px] border border-[#1F8F5C] bg-[#0E2017] px-3 py-2 text-xs text-[#3DD68C]">{message}</div>}

      <div className="min-h-0 flex-grow overflow-y-auto rounded-[10px] border p-3" style={{ background: "#12161C", borderColor: "#1F2630" }}>
        {!chain ? (
          <div className="flex h-full items-center justify-center text-sm text-[#8B96A5]">{error ? "" : "Loading chain…"}</div>
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr className="text-[9.5px] text-[#8B96A5]">
                <th className="px-1.5 py-1 text-left">CE LTP</th>
                <th className="px-1.5 py-1 text-left">CE Open</th>
                <th className="px-1.5 py-1 text-left">CE Diff%</th>
                <th className="px-1.5 py-1 text-center">Strike</th>
                <th className="px-1.5 py-1 text-right">PE Diff%</th>
                <th className="px-1.5 py-1 text-right">PE Open</th>
                <th className="px-1.5 py-1 text-right">PE LTP</th>
                <th className="px-1.5 py-1 text-right">Comb LTP</th>
                <th className="px-1.5 py-1 text-right">Comb Diff%</th>
              </tr>
            </thead>
            <tbody>
              {chain.rows.map((row) => {
                const isAtm = row.otm === 0;
                return (
                  <tr
                    key={row.strike}
                    className="mono"
                    style={isAtm ? { background: "#152538" } : undefined}
                  >
                    <td className="px-1.5 py-1 text-[#3DD68C]">{row.ce?.ltp ?? "—"}</td>
                    <td className="px-1.5 py-1 text-[#8B96A5]">{row.ce?.day_open ?? "—"}</td>
                    <td className={`px-1.5 py-1 ${(row.ce?.diff_pct ?? 0) >= 0 ? "text-[#3DD68C]" : "text-[#F85149]"}`}>
                      {row.ce ? `${row.ce.diff_pct >= 0 ? "+" : ""}${row.ce.diff_pct}%` : "—"}
                    </td>
                    <td className="px-1.5 py-1 text-center font-semibold text-[#E8EAED]">
                      {row.strike}
                      {isAtm && <span className="ml-1 text-[9px] text-[#5B9BD5]">ATM</span>}
                    </td>
                    <td className={`px-1.5 py-1 text-right ${(row.pe?.diff_pct ?? 0) >= 0 ? "text-[#3DD68C]" : "text-[#F85149]"}`}>
                      {row.pe ? `${row.pe.diff_pct >= 0 ? "+" : ""}${row.pe.diff_pct}%` : "—"}
                    </td>
                    <td className="px-1.5 py-1 text-right text-[#8B96A5]">{row.pe?.day_open ?? "—"}</td>
                    <td className="px-1.5 py-1 text-right text-[#F85149]">{row.pe?.ltp ?? "—"}</td>
                    <td className="px-1.5 py-1 text-right font-semibold">
                      {row.combined_ltp !== null ? formatCurrency(row.combined_ltp) : "—"}
                    </td>
                    <td
                      className={`px-1.5 py-1 text-right ${
                        (row.combined_diff_pct ?? 0) >= 0 ? "text-[#3DD68C]" : "text-[#F85149]"
                      }`}
                    >
                      {row.combined_diff_pct !== null ? `${row.combined_diff_pct >= 0 ? "+" : ""}${row.combined_diff_pct}%` : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
