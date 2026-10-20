"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { STRATEGY_FAMILIES, type StrategyFamily } from "@/lib/strategies";
import { fetchAvailableMargin, fetchBreadth, fetchHistoryCounts, fetchIndexQuotes, fetchOiPcr } from "@/lib/api";
import { formatCurrency } from "@/lib/format";
import { breadthVerdict } from "@/lib/breadthFormat";
import AppHeader from "./AppHeader";
import DecisionParameters from "./DecisionParameters";
import MorningBrief from "./MorningBrief";
import RecentSignalsFeed from "./RecentSignalsFeed";
import type { BreadthResponse, BreadthSnapshot, IndexQuote, OiPcrResponse } from "@/lib/types";

const POLL_MS = 15_000;

function StatTile({ label, value, tone }: { label: string; value: string; tone?: "green" | "red" | "amber" }) {
  const color = tone === "green" ? "#3DD68C" : tone === "red" ? "#F85149" : tone === "amber" ? "#F0B429" : "#E8EAED";
  return (
    <div className="rounded-[9px] border p-2.5" style={{ background: "#0E1116", borderColor: "#1A2028" }}>
      <div className="truncate text-[9.5px] text-[#8B96A5]">{label}</div>
      <div className="mono truncate text-[15px] font-bold" style={{ color }}>
        {value}
      </div>
    </div>
  );
}

/** Header row for the right-rail panels (Market Pulse, Breadth, etc.). */
function PanelHeader({ title, note }: { title: string; note?: string }) {
  return (
    <div
      className="flex shrink-0 items-center justify-between border-b px-3.5 py-2.5"
      style={{ borderColor: "#1F2630" }}
    >
      <span className="text-[11px] font-bold tracking-[0.5px] text-[#C7CDD6]">{title}</span>
      {note ? <span className="text-[10px] text-[#5B6675]">{note}</span> : null}
    </div>
  );
}

function SectionHeader({ title, note }: { title: string; note?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <h2 className="text-[13px] font-semibold uppercase tracking-[0.5px] text-[#C7CDD6]">{title}</h2>
      {note ? <span className="text-[10px] text-[#5B6675]">{note}</span> : null}
    </div>
  );
}

function BreadthSummaryCard({
  snapshot,
  total,
  asOf,
  compact = false,
}: {
  snapshot: BreadthSnapshot | null;
  total?: number;
  asOf?: string | null;
  compact?: boolean;
}) {
  const verdict = breadthVerdict(snapshot?.metrics ?? null);
  const m = snapshot?.metrics;
  const net = m ? m.advancers - m.decliners : null;
  return (
    <Link href="/breadth">
      <div
        className="rounded-[10px] border p-3.5 transition-colors hover:border-[#2E3844]"
        style={{ background: "#12161C", borderColor: "#1F2630", borderLeft: `3px solid ${verdict.color}` }}
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <span className="text-xs font-semibold text-[#C7CDD6]">MARKET BREADTH</span>
            <span className="rounded-[5px] px-2 py-[3px] text-[10px] font-bold" style={{ background: "#1B2129", color: verdict.color }}>
              {verdict.label.toUpperCase()}
            </span>
          </div>
          <span className="text-[11px] font-semibold text-[#5B9BD5]">Open Breadth →</span>
        </div>

        {m ? (
          <div
            className={`mt-2.5 grid gap-2 ${compact ? "grid-cols-3" : "grid-cols-2 sm:grid-cols-3 lg:grid-cols-6"}`}
          >
            <StatTile label="Advancers" value={String(m.advancers)} tone="green" />
            <StatTile label="Decliners" value={String(m.decliners)} tone="red" />
            <StatTile label="Net" value={net != null ? `${net >= 0 ? "+" : ""}${net}` : "—"} tone={net != null && net >= 0 ? "green" : net != null ? "red" : undefined} />
            <StatTile label="A/D Ratio" value={m.breadth_ratio != null ? m.breadth_ratio.toFixed(2) : "—"} />
            <StatTile label="% > 20-DMA" value={m.pct_above?.["20"] != null ? `${m.pct_above["20"]}%` : "—"} tone="green" />
            <StatTile label="% > 50-DMA" value={m.pct_above?.["50"] != null ? `${m.pct_above["50"]}%` : "—"} tone="amber" />
          </div>
        ) : (
          <div className="mt-2.5 text-xs text-[#8B96A5]">
            Breadth is computed by the backend worker — no snapshot yet. Open Breadth for intraday / weekly / monthly.
          </div>
        )}

        <div className="mt-2 flex items-center justify-between text-[10px] text-[#5B6675]">
          <span>{verdict.note}</span>
          <span>
            {total ? `${total} symbols` : ""}
            {total && asOf ? ` · as of ${asOf.slice(0, 10)}` : ""}
          </span>
        </div>
      </div>
    </Link>
  );
}

function StrategyCard({ family, count }: { family: StrategyFamily; count: number }) {
  const live = family.status === "live";
  return (
    <Link href={`/strategies/${family.key}`}>
      <div
        className="flex h-full cursor-pointer flex-col gap-3 rounded-[10px] border p-4 transition-colors hover:border-[#2E3844]"
        style={{ background: "#12161C", borderColor: "#1F2630", borderLeft: `3px solid ${family.accent}` }}
      >
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2.5">
            <span
              className="rounded-[5px] px-2 py-[3px] text-[11px] font-bold"
              style={{ background: family.badgeBg, color: family.badgeFg }}
            >
              {family.label.toUpperCase()}
            </span>
            <span
              className="rounded-[5px] px-1.5 py-[3px] text-[10px] font-bold"
              style={{
                background: live ? "#173324" : "#1B2129",
                color: live ? "#3DD68C" : "#8B96A5",
              }}
            >
              {live ? "LIVE" : "PLANNED"}
            </span>
          </div>
          <div className="text-right">
            <div className="text-[10px] text-[#8B96A5]">MATCHES</div>
            <div className="mono text-[15px] font-bold" style={{ color: live ? "#3DD68C" : "#8B96A5" }}>
              {count}
            </div>
          </div>
        </div>

        <div>
          <div className="text-[12px] font-semibold text-[#C7CDD6]">{family.tagline}</div>
          <div className="mt-1.5 text-xs leading-relaxed text-[#A7AFBA]">{family.description}</div>
        </div>

        <div className="mt-auto flex items-center justify-between">
          <span className="text-[11px] text-[#8B96A5]">
            {live ? "View all signals" : "Strategy in development"}
          </span>
          <span className="rounded-[7px] border border-[#232C36] px-3 py-1 text-xs text-[#8B96A5]">
            Open
          </span>
        </div>
      </div>
    </Link>
  );
}

export default function HomeView() {
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [indices, setIndices] = useState<IndexQuote[]>([]);
  const [oi, setOi] = useState<OiPcrResponse | null>(null);
  const [margin, setMargin] = useState<number | null>(null);
  const [breadth, setBreadth] = useState<BreadthResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function poll() {
      try {
        const [c, idx] = await Promise.all([fetchHistoryCounts(), fetchIndexQuotes()]);
        if (!cancelled) {
          setCounts(c);
          setIndices(idx);
        }
      } catch {
        // keep last known values
      }
      try {
        const o = await fetchOiPcr("NIFTY", 5);
        if (!cancelled) setOi(o);
      } catch {
        /* OI/PCR requires an active Definedge session */
      }
      try {
        const m = await fetchAvailableMargin();
        if (!cancelled) setMargin(m);
      } catch {
        /* margin requires an active session */
      }
      try {
        const b = await fetchBreadth();
        if (!cancelled) setBreadth(b);
      } catch {
        /* breadth appears once the backend worker has run */
      }
    }
    poll();
    const interval = setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  const index = indices[0];
  const atmIv = oi?.session_iv?.length ? oi.session_iv[oi.session_iv.length - 1].atm_iv : null;
  const latestDaily = breadth?.daily?.length ? breadth.daily[breadth.daily.length - 1] : null;

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-[#0B0E11] text-[#E8EAED]">
      <AppHeader
        right={
          index && (
            <div className="flex items-baseline gap-2">
              <span className="text-[13px] text-[#8B96A5]">{index.symbol}</span>
              <span className="mono text-lg font-bold">{index.spot.toLocaleString("en-IN")}</span>
              <span className={`mono text-xs font-semibold ${index.change_pct >= 0 ? "text-[#3DD68C]" : "text-[#F85149]"}`}>
                {index.change_pct >= 0 ? "+" : ""}
                {index.change_pct.toFixed(2)}%
              </span>
            </div>
          )
        }
      />

      {/* Body — full width, no max-width cap */}
      <div className="min-h-0 flex-grow overflow-hidden px-6 py-5">
        <div className="flex h-full min-h-0 gap-5">
          {/* ── Main column (fluid, stretches edge-to-edge) ── */}
          <div className="flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto">
            <MorningBrief />

            <SectionHeader title="Strategy Directory" note="Open a feed to drill into the live signals." />

            <div className="grid grid-cols-1 gap-3.5 sm:grid-cols-2 xl:grid-cols-3">
              {STRATEGY_FAMILIES.map((f) => {
                const count = f.strategyTypes.reduce((n, t) => n + (counts[t] ?? 0), 0);
                return <StrategyCard key={f.key} family={f} count={count} />;
              })}
            </div>

            <RecentSignalsFeed className="min-h-[260px] flex-1" />
          </div>

          {/* ── Right rail (fixed width, context always visible) ── */}
          <div className="flex w-[380px] shrink-0 flex-col gap-5 overflow-y-auto">
            {/* Market pulse */}
            <div className="rounded-[10px] border" style={{ background: "#12161C", borderColor: "#1F2630" }}>
              <PanelHeader title="MARKET PULSE" note="session context" />
              <div className="grid grid-cols-3 gap-2 p-3">
                <StatTile label="NIFTY Spot" value={index ? index.spot.toLocaleString("en-IN") : "—"} />
                <StatTile
                  label="Day Change"
                  value={index ? `${index.change_pct >= 0 ? "+" : ""}${index.change_pct.toFixed(2)}%` : "—"}
                  tone={index ? (index.change_pct >= 0 ? "green" : "red") : undefined}
                />
                <StatTile
                  label="OI PCR"
                  value={oi?.oi_pcr != null ? oi.oi_pcr.toFixed(2) : "—"}
                  tone={oi?.oi_pcr != null && oi.oi_pcr > 1 ? "green" : oi?.oi_pcr != null ? "red" : undefined}
                />
                <StatTile label="Vol PCR" value={oi?.vol_pcr != null ? oi.vol_pcr.toFixed(2) : "—"} />
                <StatTile label="ATM IV" value={atmIv != null ? `${(atmIv * 100).toFixed(1)}%` : "—"} />
                <StatTile label="Margin" value={margin !== null ? formatCurrency(margin) : "—"} />
              </div>
            </div>

            {/* Breadth */}
            <BreadthSummaryCard compact snapshot={latestDaily} total={breadth?.total} asOf={breadth?.as_of} />

            {/* Decision parameters */}
            <DecisionParameters />
          </div>
        </div>
      </div>
    </div>
  );
}
