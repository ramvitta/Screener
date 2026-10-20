"use client";

import { useEffect, useMemo, useState } from "react";
import OpenAlgoChart from "./charts/OpenAlgoChart";
import { fetchTelegramCallChart } from "@/lib/api";
import { toOpenAlgoBars } from "@/lib/charts";
import type { TelegramCall, TelegramCallChartResponse } from "@/lib/types";

const COLORS = {
  entry: "#5AB0FF",
  stop: "#F85149",
  target: "#3DD68C",
};

function fmtPrice(value?: number | null): string {
  if (value == null || Number.isNaN(value)) return "—";
  return Number(value).toLocaleString("en-IN", { maximumFractionDigits: 2 });
}

function fmtTime(value?: string | null): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

function fmtDate(value?: string | null): string {
  if (!value) return "—";
  const d = new Date(`${value}T00:00:00+05:30`);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric", timeZone: "Asia/Kolkata" });
}

function fmtDecimal(value?: number | null): string {
  if (value == null || Number.isNaN(value)) return "—";
  return Number(value).toLocaleString("en-IN", { maximumFractionDigits: 4 });
}

const SEGMENT_LABELS: Record<string, string> = {
  index: "Index spot",
  index_option: "Index option",
  index_future: "Index future",
  equity: "Cash equity",
  stock_option: "Stock option",
  stock_future: "Stock future",
  commodity: "Commodity",
  commodity_option: "Commodity option",
  commodity_future: "Commodity future",
  other: "Contract",
};

const MASTER_TYPE_LABELS: Record<string, string> = {
  IDX: "Index spot",
  EQ: "Cash equity",
  OPTIDX: "Index option",
  FUTIDX: "Index future",
  OPTSTK: "Stock option",
  FUTSTK: "Stock future",
  OPTFUT: "Commodity option",
  FUTCOM: "Commodity future",
};

function instrumentKind(segment?: string | null, instrumentType?: string | null): string {
  return SEGMENT_LABELS[segment ?? ""] ?? MASTER_TYPE_LABELS[instrumentType ?? ""] ?? "Contract";
}

function Detail({ label, value, mono = false }: { label: string; value?: string | number | null; mono?: boolean }) {
  if (value == null || value === "") return null;
  return (
    <div className="flex min-w-0 items-baseline gap-1.5">
      <span className="shrink-0 text-[#5B6675]">{label}</span>
      <span className={`min-w-0 break-all text-[#C7CDD6] ${mono ? "font-mono" : ""}`}>{value}</span>
    </div>
  );
}

/** Modal 5-minute chart for one tracked call. The backend resolves the actual
 * contract (strike/expiry included) and returns candles plus the call's entry,
 * stop and target levels; this component only draws them. */
export default function TelegramCallChart({ call, onClose }: { call: TelegramCall; onClose: () => void }) {
  const [data, setData] = useState<TelegramCallChartResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchTelegramCallChart(call.id, 5)
      .then((result) => {
        if (!cancelled) setData(result);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Could not load chart");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [call.id]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const bars = useMemo(() => (data ? toOpenAlgoBars(data.candles) : []), [data]);

  const verticalLines = useMemo(() => {
    const posted = data?.posted_at_utc_seconds ?? (data?.posted_at ? Math.floor(Date.parse(data.posted_at) / 1000) : null);
    return posted != null && Number.isFinite(posted)
      ? [{ time: posted, color: "#F0B429", lineWidth: 1.5, dashed: true }]
      : [];
  }, [data]);

  const instrumentDetails = useMemo(() => {
    if (!data) return [];
    return [
      { label: "Instrument", value: instrumentKind(data.segment, data.instrument_type) },
      { label: "Exchange", value: data.exchange },
      { label: "Underlying", value: data.underlying ?? call.symbol },
      { label: "Expiry", value: data.expiry ? fmtDate(data.expiry) : null },
      { label: "Strike", value: data.strike != null ? fmtPrice(data.strike) : call.strike != null ? fmtPrice(call.strike) : null },
      { label: "Option", value: data.option_type ?? call.option_type },
      { label: "Lot size", value: data.lot_size != null ? String(data.lot_size) : null },
      { label: "Tick size", value: data.tick_size != null ? fmtDecimal(data.tick_size) : null },
      { label: "Trade", value: `${data.action ?? call.action} · ${data.direction ?? call.direction}` },
      { label: "Call status", value: data.status ?? call.status },
    ];
  }, [data, call]);

  const priceLines = useMemo(() => {
    if (!data) return [];
    const { levels } = data;
    const lines: {
      id: string;
      price: number;
      color: string;
      label?: string;
      leftLabel?: string;
      badge?: string;
      dashed?: boolean;
      lineWidth?: number;
    }[] = [];
    if (levels.entry != null) {
      const range =
        levels.entry_low != null && levels.entry_high != null && levels.entry_low !== levels.entry_high
          ? `${fmtPrice(levels.entry_low)}–${fmtPrice(levels.entry_high)}`
          : fmtPrice(levels.entry);
      lines.push({
        id: "entry",
        price: levels.entry,
        color: COLORS.entry,
        badge: "ENTRY",
        leftLabel: `entry ${range}`,
        dashed: false,
        lineWidth: 1.5,
      });
    }
    if (levels.stop_loss != null) {
      lines.push({
        id: "stop-loss",
        price: levels.stop_loss,
        color: COLORS.stop,
        badge: "SL",
        leftLabel: `SL ${fmtPrice(levels.stop_loss)}`,
        dashed: true,
      });
    }
    levels.targets.forEach((target, index) => {
      lines.push({
        id: `target-${index + 1}`,
        price: target,
        color: COLORS.target,
        badge: `T${index + 1}`,
        leftLabel: `target ${fmtPrice(target)}`,
        dashed: true,
      });
    });
    return lines;
  }, [data]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      onMouseDown={onClose}
      role="presentation"
    >
      <div
        className="flex max-h-[92vh] w-full max-w-[1180px] flex-col overflow-hidden rounded-[12px] border shadow-2xl"
        style={{ background: "#12161C", borderColor: "#2A333F" }}
        onMouseDown={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <div className="flex items-start justify-between gap-3 border-b px-4 py-3" style={{ borderColor: "#1F2630" }}>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-bold text-[#E8EAED]">{data?.label ?? call.symbol}</span>
              <span className="rounded-[5px] bg-[#1B2A3A] px-2 py-[3px] text-[10px] font-bold text-[#5AB0FF]">5m candles</span>
              {data?.trading_symbol && (
                <span className="text-[10px] text-[#5B6675]">{data.exchange} · {data.trading_symbol}</span>
              )}
            </div>
            <div className="mt-1 text-[10px] text-[#8B96A5]">
              {call.chat_title || call.chat_username} · call posted {fmtTime(call.posted_at)}
              {data?.last_candle_at ? ` · last candle ${fmtTime(data.last_candle_at)}` : ""}
            </div>
            <div className="mt-2 flex flex-wrap gap-2 text-[10px]">
              <span className="rounded-[5px] bg-[#12213A] px-2 py-[3px] text-[#5AB0FF]">
                Entry {data?.levels.entry != null ? fmtPrice(data.levels.entry) : fmtPrice(call.entry_price)}
              </span>
              <span className="rounded-[5px] bg-[#2A1417] px-2 py-[3px] text-[#F85149]">
                SL {fmtPrice(data?.levels.stop_loss ?? call.stop_loss)}
              </span>
              {(data?.levels.targets?.length ? data.levels.targets : [call.target_1, call.target_2, call.target_3])
                .filter((v): v is number => v != null)
                .map((target, index) => (
                  <span key={`${target}-${index}`} className="rounded-[5px] bg-[#10281D] px-2 py-[3px] text-[#3DD68C]">
                    T{index + 1} {fmtPrice(target)}
                  </span>
                ))}
            </div>
            {data && (
              <div
                className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 border-t pt-2 text-[10px] sm:grid-cols-4"
                style={{ borderColor: "#1F2630" }}
              >
                {instrumentDetails.map((detail) => (
                  <Detail key={detail.label} label={detail.label} value={detail.value} />
                ))}
              </div>
            )}
          </div>
          <button
            onClick={onClose}
            className="rounded-[6px] border px-2.5 py-1 text-xs text-[#8B96A5] hover:text-[#E8EAED]"
            style={{ borderColor: "#2E3844" }}
            aria-label="Close chart"
          >
            ✕
          </button>
        </div>

        <div className="p-2" style={{ height: "clamp(360px, 62vh, 540px)" }}>
          {loading ? (
            <div className="flex h-full items-center justify-center text-xs text-[#8B96A5]">Loading 5-minute chart…</div>
          ) : error ? (
            <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center">
              <div className="text-xs text-[#F85149]">{error}</div>
              <div className="text-[10px] text-[#5B6675]">
                The call is still recorded; only the chart could not resolve the contract or broker history.
              </div>
            </div>
          ) : bars.length === 0 ? (
            <div className="flex h-full items-center justify-center text-xs text-[#8B96A5]">
              No 5-minute candles were returned for this contract/window.
            </div>
          ) : (
            <OpenAlgoChart seriesType="candlestick" data={bars} priceLines={priceLines} verticalLines={verticalLines} />
          )}
        </div>
      </div>
    </div>
  );
}
