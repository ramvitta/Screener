"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import AppHeader from "./AppHeader";
import { fetchBreadth } from "@/lib/api";
import { breadthVerdict, breadthLabelFor, type Bucket } from "@/lib/breadthFormat";
import type { BreadthResponse, BreadthSnapshot } from "@/lib/types";

const POLL_MS = 60_000;

const BUCKET_LABEL: Record<Bucket, string> = {
  hour: "Intraday (hourly)",
  day: "Daily",
  week: "Weekly",
  month: "Monthly",
};

function Delta({ prev, cur }: { prev: number | null | undefined; cur: number | null | undefined }) {
  if (prev == null || cur == null || prev === cur) return null;
  const up = cur > prev;
  return (
    <span className="mono ml-1 text-[10px]" style={{ color: up ? "#3DD68C" : "#F85149" }}>
      {up ? "▲" : "▼"}
    </span>
  );
}

function Tile({
  label,
  value,
  tone,
  deltaEl,
}: {
  label: string;
  value: string;
  tone?: "green" | "red" | "amber" | "blue";
  deltaEl?: ReactNode;
}) {
  const color =
    tone === "green" ? "#3DD68C" : tone === "red" ? "#F85149" : tone === "amber" ? "#F0B429" : tone === "blue" ? "#5B9BD5" : "#E8EAED";
  return (
    <div className="rounded-[9px] border p-3" style={{ background: "#0E1116", borderColor: "#1A2028" }}>
      <div className="text-[10px] text-[#8B96A5]">{label}</div>
      <div className="mono mt-0.5 flex items-center text-lg font-bold" style={{ color }}>
        {value}
        {deltaEl}
      </div>
    </div>
  );
}

function LineChart({
  points,
  color,
  height = 160,
  yLabel,
  refLine,
  band,
  labelFn,
}: {
  points: { label: string; value: number | null }[];
  color: string;
  height?: number;
  yLabel?: string;
  refLine?: number;
  band?: { lo: number; hi: number };
  labelFn: (ts: string) => string;
}) {
  const width = 720;
  const pad = { l: 36, r: 12, t: 12, b: 24 };
  const values = points.map((p) => p.value).filter((v): v is number => v != null);
  if (values.length < 2) {
    return <div className="flex h-40 items-center justify-center text-xs text-[#5B6675]">Not enough data yet.</div>;
  }
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const innerW = width - pad.l - pad.r;
  const innerH = height - pad.t - pad.b;
  const x = (i: number) => pad.l + (i / (points.length - 1)) * innerW;
  const y = (v: number) => pad.t + innerH - ((v - min) / span) * innerH;
  const path = points
    .map((p, i) => (p.value == null ? null : `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`))
    .filter(Boolean)
    .join(" ");

  return (
    <div>
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full" preserveAspectRatio="none" style={{ height }}>
        {band && (
          <rect
            x={pad.l}
            y={y(band.hi)}
            width={innerW}
            height={Math.max(y(band.lo) - y(band.hi), 0)}
            fill="#232C36"
            opacity={0.35}
          />
        )}
        <line x1={pad.l} y1={pad.t} x2={pad.l} y2={height - pad.b} stroke="#232C36" strokeWidth={1} />
        <line x1={pad.l} y1={height - pad.b} x2={width - pad.r} y2={height - pad.b} stroke="#232C36" strokeWidth={1} />
        {refLine != null && refLine >= min && refLine <= max && (
          <line x1={pad.l} y1={y(refLine)} x2={width - pad.r} y2={y(refLine)} stroke="#5B6675" strokeWidth={1} strokeDasharray="3 3" />
        )}
        <text x={6} y={pad.t + 3} fill="#5B6675" fontSize={9}>
          {max.toFixed(1)}
        </text>
        <text x={6} y={height - pad.b} fill="#5B6675" fontSize={9}>
          {min.toFixed(1)}
        </text>
        <path d={path} fill="none" stroke={color} strokeWidth={1.75} />
        {points
          .filter((p) => p.value != null)
          .map((p, i) => (
            <circle key={i} cx={x(i)} cy={y(p.value as number)} r={2} fill={color} />
          ))}
        {points.map((p, i) =>
          i % Math.max(1, Math.floor(points.length / 8)) === 0 ? (
            <text key={i} x={x(i)} y={height - pad.b + 14} fill="#5B6675" fontSize={8} textAnchor="middle">
              {labelFn(p.label)}
            </text>
          ) : null,
        )}
      </svg>
      {yLabel && <div className="mt-1 text-[10px] text-[#5B6675]">{yLabel}</div>}
    </div>
  );
}

function AdvDecBars({ snapshots, labelFn }: { snapshots: BreadthSnapshot[]; labelFn: (ts: string) => string }) {
  const recent = snapshots.slice(-20);
  return (
    <div>
      <div className="flex h-24 items-end gap-[2px]">
        {recent.map((s, i) => {
          const total = Math.max(s.metrics.advancers + s.metrics.decliners + s.metrics.unchanged, 1);
          const adv = (s.metrics.advancers / total) * 100;
          const dec = (s.metrics.decliners / total) * 100;
          return (
            <div key={i} className="flex flex-1 flex-col items-stretch gap-[1px]" title={`${s.ts}: ${s.metrics.advancers}A / ${s.metrics.decliners}D`}>
              <div className="w-full rounded-t-[2px]" style={{ height: `${adv}%`, background: "#3DD68C", minHeight: 0 }} />
              <div className="w-full rounded-b-[2px]" style={{ height: `${dec}%`, background: "#F85149", minHeight: 0 }} />
            </div>
          );
        })}
      </div>
      <div className="mt-1 flex justify-between text-[9px] text-[#5B6675]">
        <span>{labelFn(recent[0]?.ts ?? "")}</span>
        <span>{labelFn(recent[recent.length - 1]?.ts ?? "")}</span>
      </div>
    </div>
  );
}

export default function BreadthView() {
  const [data, setData] = useState<BreadthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [bucket, setBucket] = useState<Bucket>("day");

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const b = await fetchBreadth();
        if (!cancelled) {
          setData(b);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Could not load breadth");
      }
    }
    load();
    const id = setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const series = useMemo<BreadthSnapshot[]>(() => {
    if (!data) return [];
    if (bucket === "hour") return data.intraday;
    if (bucket === "week") return data.weekly;
    if (bucket === "month") return data.monthly;
    return data.daily;
  }, [data, bucket]);

  const latest = series[series.length - 1];
  const prev = series[series.length - 2];
  const latestDaily = data?.daily?.length ? data.daily[data.daily.length - 1] : null;
  const verdict = breadthVerdict(latestDaily ? latestDaily.metrics : null);

  const net = latest ? latest.metrics.advancers - latest.metrics.decliners : null;
  const prevNet = prev ? prev.metrics.advancers - prev.metrics.decliners : null;

  const ratioPoints = series.slice(-40).map((s) => ({ label: s.ts, value: s.metrics.breadth_ratio }));
  const dmaLines = useMemo(() => {
    const periods = ["20", "50", "200"];
    return periods.map((p) => ({
      period: p,
      color: p === "20" ? "#3DD68C" : p === "50" ? "#F0B429" : "#5B9BD5",
      points: series.slice(-40).map((s) => ({ label: s.ts, value: s.metrics.pct_above?.[p] ?? null })),
    }));
  }, [series]);

  const labelFn = (ts: string) => breadthLabelFor(ts, bucket);

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-[#0B0E11] text-[#E8EAED]">
      <AppHeader />
      <div className="min-h-0 flex-grow overflow-y-auto px-6 py-5">
        <div className="flex flex-col gap-5">
          <div>
            <h1 className="text-xl font-semibold">Market Breadth</h1>
            <p className="mt-1 text-sm text-[#8B96A5]">
              Advance / decline and the share of stocks above their moving averages, across intraday, daily, weekly and monthly windows.
            </p>
          </div>

          {error && (
            <div className="rounded-[8px] border border-[#F85149]/40 bg-[#F85149]/10 p-3 text-xs text-[#F85149]">
              {error} — breadth is computed by the backend worker and appears once it has run.
            </div>
          )}

          {/* Verdict banner */}
          <div
            className="rounded-[10px] border p-4"
            style={{ background: "#12161C", borderColor: "#1F2630", borderLeft: `4px solid ${verdict.color}` }}
          >
            <div className="flex items-center gap-3">
              <span className="text-sm font-bold" style={{ color: verdict.color }}>
                {verdict.label.toUpperCase()}
              </span>
              <span className="text-xs text-[#8B96A5]">{verdict.note}</span>
            </div>
          </div>

          {/* Universe + latest snapshot */}
          <div className="rounded-[10px] border p-3.5" style={{ background: "#12161C", borderColor: "#1F2630" }}>
            <div className="mb-2.5 flex items-center justify-between">
              <div className="text-xs font-semibold text-[#C7CDD6]">LATEST SNAPSHOT</div>
              <div className="text-[10px] text-[#8B96A5]">
                {data ? `${data.total} symbols · ${data.universe.toUpperCase()}` : "—"}
                {data?.as_of ? ` · as of ${data.as_of.slice(0, 10)}` : ""}
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-7">
              <Tile
                label="Advancers"
                value={latest ? String(latest.metrics.advancers) : "—"}
                tone="green"
                deltaEl={<Delta prev={prev?.metrics.advancers} cur={latest?.metrics.advancers} />}
              />
              <Tile
                label="Decliners"
                value={latest ? String(latest.metrics.decliners) : "—"}
                tone="red"
                deltaEl={<Delta prev={prev?.metrics.decliners} cur={latest?.metrics.decliners} />}
              />
              <Tile
                label="Net Advance"
                value={net != null ? `${net >= 0 ? "+" : ""}${net}` : "—"}
                tone={net != null && net >= 0 ? "green" : net != null ? "red" : undefined}
                deltaEl={<Delta prev={prevNet} cur={net} />}
              />
              <Tile
                label="A/D Ratio"
                value={latest?.metrics.breadth_ratio != null ? latest.metrics.breadth_ratio.toFixed(2) : "—"}
                tone="blue"
              />
              <Tile
                label="% > 20-DMA"
                value={latest?.metrics.pct_above?.["20"] != null ? `${latest.metrics.pct_above["20"]}%` : "—"}
                tone="green"
                deltaEl={<Delta prev={prev?.metrics.pct_above?.["20"]} cur={latest?.metrics.pct_above?.["20"]} />}
              />
              <Tile
                label="% > 50-DMA"
                value={latest?.metrics.pct_above?.["50"] != null ? `${latest.metrics.pct_above["50"]}%` : "—"}
                tone="amber"
                deltaEl={<Delta prev={prev?.metrics.pct_above?.["50"]} cur={latest?.metrics.pct_above?.["50"]} />}
              />
              <Tile
                label="% > 200-DMA"
                value={latest?.metrics.pct_above?.["200"] != null ? `${latest.metrics.pct_above["200"]}%` : "—"}
                tone="blue"
                deltaEl={<Delta prev={prev?.metrics.pct_above?.["200"]} cur={latest?.metrics.pct_above?.["200"]} />}
              />
            </div>
          </div>

          <div className="flex items-center gap-1 self-start rounded-[8px] border border-[#1F2630] bg-[#12161C] p-1">
            {(["hour", "day", "week", "month"] as Bucket[]).map((b) => (
              <button
                key={b}
                onClick={() => setBucket(b)}
                className="rounded-[6px] px-3 py-1 text-xs font-medium"
                style={{ background: bucket === b ? "#1B2129" : "transparent", color: bucket === b ? "#C7CDD6" : "#8B96A5" }}
              >
                {BUCKET_LABEL[b]}
              </button>
            ))}
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <div className="rounded-[10px] border p-3.5" style={{ background: "#12161C", borderColor: "#1F2630" }}>
              <div className="mb-2 flex items-center justify-between">
                <div className="text-xs font-semibold text-[#C7CDD6]">ADVANCE / DECLINE</div>
                <div className="flex items-center gap-3 text-[10px] text-[#8B96A5]">
                  <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-[#3DD68C]" />Advancers</span>
                  <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-[#F85149]" />Decliners</span>
                </div>
              </div>
              <AdvDecBars snapshots={series} labelFn={labelFn} />
              <div className="mt-2 text-[10px] text-[#5B6675]">Share of universe advancing vs declining, last 20 buckets.</div>
            </div>

            <div className="rounded-[10px] border p-3.5" style={{ background: "#12161C", borderColor: "#1F2630" }}>
              <div className="mb-2 text-xs font-semibold text-[#C7CDD6]">A/D RATIO</div>
              <LineChart
                points={ratioPoints}
                color="#5B9BD5"
                refLine={1}
                labelFn={labelFn}
                yLabel="Advancers ÷ Decliners. The dashed line marks 1.0 — above it, advancers lead."
              />
            </div>
          </div>

          <div className="rounded-[10px] border p-3.5" style={{ background: "#12161C", borderColor: "#1F2630" }}>
            <div className="mb-2 flex items-center justify-between">
              <div className="text-xs font-semibold text-[#C7CDD6]">% ABOVE MOVING AVERAGE</div>
              <div className="flex items-center gap-3 text-[10px] text-[#8B96A5]">
                {dmaLines.map((l) => (
                  <span key={l.period} className="flex items-center gap-1">
                    <span className="h-2 w-2 rounded-full" style={{ background: l.color }} />
                    {l.period}-DMA
                  </span>
                ))}
              </div>
            </div>
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
              {dmaLines.map((l) => (
                <LineChart
                  key={l.period}
                  points={l.points}
                  color={l.color}
                  height={120}
                  refLine={50}
                  band={{ lo: 30, hi: 70 }}
                  labelFn={labelFn}
                />
              ))}
            </div>
            <div className="mt-2 text-[10px] text-[#5B6675]">
              The dashed line marks 50% — the share of the universe above its {""}
              {dmaLines.map((l) => l.period).join("/")}-day moving average. Above 50% = broad uptrend participation.
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
