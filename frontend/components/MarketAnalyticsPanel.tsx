"use client";

import { useEffect, useState } from "react";
import SessionIvChart from "./charts/SessionIvChart";
import { fetchKellySizing, fetchOiPcr } from "@/lib/api";
import { formatCurrency, formatDateTime } from "@/lib/format";
import type { KellySizingResponse, OiPcrResponse } from "@/lib/types";

const OI_POLL_MS = 60_000;
const CAPITAL_STORAGE_KEY = "optionscreen.capital";

function StatTile({ label, value, tone }: { label: string; value: string; tone?: "green" | "red" | "amber" }) {
  const color = tone === "green" ? "#3DD68C" : tone === "red" ? "#F85149" : tone === "amber" ? "#F0B429" : "#E8EAED";
  return (
    <div className="rounded-[9px] border p-2.5" style={{ background: "#0E1116", borderColor: "#1A2028" }}>
      <div className="text-[9.5px] text-[#8B96A5]">{label}</div>
      <div className="mono text-base font-bold" style={{ color }}>
        {value}
      </div>
    </div>
  );
}

/** Phase 5 of the UI gap-closing plan: Kelly-style live sizing, OI change /
 * PCR, and a real intraday ATM-IV series. All derived from real data — Kelly
 * from the user's own logged legs, OI/IV from the historical-candle
 * endpoint (Definedge's live quotes carry no OI field at all). */
function readSavedCapital(): string {
  if (typeof window === "undefined") return "1000000";
  try {
    return window.localStorage.getItem(CAPITAL_STORAGE_KEY) ?? "1000000";
  } catch {
    return "1000000"; // private-browsing / blocked storage just keeps the default
  }
}

export default function MarketAnalyticsPanel() {
  const [capitalInput, setCapitalInput] = useState(readSavedCapital);
  const [kelly, setKelly] = useState<KellySizingResponse | null>(null);
  const [kellyError, setKellyError] = useState<string | null>(null);
  const [oi, setOi] = useState<OiPcrResponse | null>(null);
  const [oiError, setOiError] = useState<string | null>(null);

  const capital = Number(capitalInput);
  const capitalValid = Number.isFinite(capital) && capital > 0;

  useEffect(() => {
    if (!capitalValid) return;
    let cancelled = false;
    fetchKellySizing(capital)
      .then((k) => {
        if (!cancelled) {
          setKelly(k);
          setKellyError(null);
        }
      })
      .catch(() => {
        if (!cancelled) setKellyError("Could not load sizing — is the backend reachable?");
      });
    try {
      window.localStorage.setItem(CAPITAL_STORAGE_KEY, capitalInput);
    } catch {
      // ignore
    }
    return () => {
      cancelled = true;
    };
  }, [capitalInput, capital, capitalValid]);

  useEffect(() => {
    let cancelled = false;
    async function poll() {
      try {
        const data = await fetchOiPcr("NIFTY", 5);
        if (!cancelled) {
          setOi(data);
          setOiError(null);
        }
      } catch {
        if (!cancelled) setOiError("Could not load OI/PCR — is the backend logged in to Definedge?");
      }
    }
    poll();
    const interval = setInterval(poll, OI_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  return (
    <div className="flex flex-col gap-3">
      {/* Kelly sizing */}
      <div className="rounded-[10px] border p-3.5" style={{ background: "#12161C", borderColor: "#1F2630" }}>
        <div className="mb-2.5 flex items-center justify-between">
          <div className="text-xs font-semibold text-[#C7CDD6]">KELLY CRITERION LIVE SIZING</div>
          <div className="flex items-center gap-1.5 text-[11px] text-[#8B96A5]">
            Capital
            <input
              type="number"
              value={capitalInput}
              onChange={(e) => setCapitalInput(e.target.value)}
              className="mono w-28 rounded-[5px] border border-[#232C36] bg-[#0E1116] px-1.5 py-1 text-[11px] text-[#E8EAED]"
            />
          </div>
        </div>
        {kellyError && <div className="text-xs text-amber-300">{kellyError}</div>}
        {kelly && (
          <>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <StatTile label="Live Assignment Exposure" value={`${formatCurrency(kelly.assignment_exposure)} (${kelly.exposure_pct_of_capital}%)`} tone={kelly.over_allocated ? "red" : undefined} />
              <StatTile label="Live Premium Collected" value={formatCurrency(kelly.premium_collected)} tone="green" />
              <StatTile label="Managed SL Risk" value={`${formatCurrency(kelly.managed_sl_risk)} (${kelly.managed_sl_risk_pct_of_capital}%)`} tone="amber" />
              <StatTile label="Open Legs" value={String(kelly.open_leg_count)} />
            </div>
            {kelly.over_allocated && (
              <div className="mt-2 rounded-[7px] border border-[#3A1F22] bg-[#1F1013] px-2.5 py-1.5 text-[11px] text-[#F85149]">
                ⚠ Over-Allocated / High Concentration — exposure exceeds {150}% of capital
              </div>
            )}
          </>
        )}
      </div>

      {/* OI / PCR */}
      <div className="rounded-[10px] border p-3.5" style={{ background: "#12161C", borderColor: "#1F2630" }}>
        <div className="mb-2.5 flex flex-wrap items-center justify-between gap-2">
          <div className="text-xs font-semibold text-[#C7CDD6]">OI CHANGE / PCR</div>
          {oi && (
            <span className="text-[11px] text-[#8B96A5]">
              {oi.symbol} · Exp {new Date(oi.expiry).toLocaleDateString("en-IN", { day: "2-digit", month: "short" })} · as of{" "}
              {oi.as_of ? formatDateTime(oi.as_of) : "—"}
            </span>
          )}
        </div>
        {oiError && <div className="text-xs text-amber-300">{oiError}</div>}
        {oi && (
          <>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <StatTile label="Call OI" value={oi.call_oi_total.toLocaleString("en-IN")} />
              <StatTile label="Put OI" value={oi.put_oi_total.toLocaleString("en-IN")} />
              <StatTile label="OI PCR" value={oi.oi_pcr?.toFixed(2) ?? "—"} tone={oi.oi_pcr && oi.oi_pcr > 1 ? "green" : "red"} />
              <StatTile label="Vol PCR" value={oi.vol_pcr?.toFixed(2) ?? "—"} />
            </div>
            <div className="mt-3 flex flex-col gap-1">
              <div className="grid grid-cols-5 gap-1.5 px-1 text-[9.5px] text-[#8B96A5]">
                <span>Strike</span>
                <span className="text-right">Call OI</span>
                <span className="text-right">Call ΔOI</span>
                <span className="text-right">Put OI</span>
                <span className="text-right">Put ΔOI</span>
              </div>
              {oi.rows.map((r) => (
                <div
                  key={r.strike}
                  className="mono grid grid-cols-5 gap-1.5 rounded-[6px] px-1.5 py-1 text-[11px]"
                  style={r.otm === 0 ? { background: "#152538" } : undefined}
                >
                  <span className="font-semibold text-[#E8EAED]">
                    {r.strike}
                    {r.otm === 0 && <span className="ml-1 text-[9px] text-[#5B9BD5]">ATM</span>}
                  </span>
                  <span className="text-right">{r.call_oi?.toLocaleString("en-IN") ?? "—"}</span>
                  <span className={`text-right ${(r.call_oi_change ?? 0) >= 0 ? "text-[#3DD68C]" : "text-[#F85149]"}`}>
                    {r.call_oi_change !== null ? r.call_oi_change.toLocaleString("en-IN") : "—"}
                  </span>
                  <span className="text-right">{r.put_oi?.toLocaleString("en-IN") ?? "—"}</span>
                  <span className={`text-right ${(r.put_oi_change ?? 0) >= 0 ? "text-[#3DD68C]" : "text-[#F85149]"}`}>
                    {r.put_oi_change !== null ? r.put_oi_change.toLocaleString("en-IN") : "—"}
                  </span>
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      {/* Session IV */}
      <div className="rounded-[10px] border p-3.5" style={{ background: "#12161C", borderColor: "#1F2630" }}>
        <div className="mb-2 text-xs font-semibold text-[#C7CDD6]">SESSION IV · ATM CE</div>
        <div className="h-[140px]">{oi ? <SessionIvChart points={oi.session_iv} /> : <div className="flex h-full items-center justify-center text-sm text-[#8B96A5]">Loading…</div>}</div>
      </div>
    </div>
  );
}
