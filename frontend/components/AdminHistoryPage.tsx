"use client";

import { Fragment, useEffect, useState } from "react";
import Link from "next/link";
import {
  createDownloadJob,
  fetchDownloadCoverage,
  fetchDownloadJob,
  fetchDownloadJobItems,
  fetchDownloadJobs,
  fetchFnoStocks,
  stopDownloadJob,
  validateDownload,
} from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type {
  DownloadCoverageRow,
  DownloadJobItemStatus,
  DownloadJobStatus,
  DownloadValidateResponse,
  FnoStock,
} from "@/lib/types";

const JOB_POLL_MS = 2000;

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function daysAgoIso(days: number): string {
  return new Date(Date.now() - days * 86_400_000).toISOString().slice(0, 10);
}

/** Admin page: proactive historical-data archival for F&O stocks. Not a
 * retroactive backfill tool — a spike confirmed Definedge's history
 * endpoint stops serving a contract once it expires off the live
 * instrument master, so this only archives currently-listed equity/
 * future/option contracts before they roll off (see
 * backend/app/integrations/history_downloader.py). */
export default function AdminHistoryPage() {
  const [stocks, setStocks] = useState<FnoStock[]>([]);
  const [symbol, setSymbol] = useState("");
  const [startDate, setStartDate] = useState(daysAgoIso(30));
  const [endDate, setEndDate] = useState(todayIso());
  const [includeEquity, setIncludeEquity] = useState(true);
  const [includeFutures, setIncludeFutures] = useState(true);
  const [includeOptions, setIncludeOptions] = useState(true);
  const [strikeBand, setStrikeBand] = useState(5);

  const [validation, setValidation] = useState<DownloadValidateResponse | null>(null);
  const [validating, setValidating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [activeJob, setActiveJob] = useState<DownloadJobStatus | null>(null);
  const [activeJobItems, setActiveJobItems] = useState<DownloadJobItemStatus[]>([]);
  const [recentJobs, setRecentJobs] = useState<DownloadJobStatus[]>([]);
  const [coverage, setCoverage] = useState<DownloadCoverageRow[]>([]);

  useEffect(() => {
    fetchFnoStocks()
      .then((s) => {
        setStocks(s);
        if (s.length && !symbol) setSymbol(s[0].symbol);
      })
      .catch(() => setError("Could not load F&O stock list — is the backend logged in to Definedge?"));
    fetchDownloadJobs().then(setRecentJobs).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!activeJob || activeJob.status === "completed" || activeJob.status === "failed" || activeJob.status === "cancelled") return;
    let cancelled = false;
    const interval = setInterval(async () => {
      try {
        const [job, items] = await Promise.all([fetchDownloadJob(activeJob.id), fetchDownloadJobItems(activeJob.id)]);
        if (!cancelled) {
          setActiveJob(job);
          setActiveJobItems(items);
        }
      } catch {
        // keep showing the last known state
      }
    }, JOB_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [activeJob]);

  const scope = { equity: includeEquity, futures: includeFutures, options: includeOptions, strike_band: strikeBand };

  async function handleValidate() {
    if (!symbol) return;
    setValidating(true);
    setError(null);
    try {
      const result = await validateDownload(symbol, startDate, endDate, scope);
      setValidation(result);
      const cov = await fetchDownloadCoverage(symbol);
      setCoverage(cov);
    } catch {
      setError("Validation failed — is the backend logged in to Definedge?");
    } finally {
      setValidating(false);
    }
  }

  async function handleStart() {
    if (!symbol) return;
    setError(null);
    try {
      const job = await createDownloadJob(symbol, startDate, endDate, scope);
      setActiveJob(job);
      setActiveJobItems([]);
      setRecentJobs((prev) => [job, ...prev].slice(0, 50));
    } catch {
      setError("Could not start the download job.");
    }
  }

  async function handleStop() {
    if (!activeJob) return;
    try {
      const job = await stopDownloadJob(activeJob.id);
      setActiveJob(job);
    } catch {
      // ignore — polling will reflect the real state
    }
  }

  return (
    <div className="flex h-screen flex-col overflow-y-auto bg-[#0B0E11] px-6 py-5 text-[#E8EAED]">
      <div className="mb-4 flex items-center gap-3">
        <Link href="/" className="text-xs text-[#5B9BD5]">
          ← Dashboard
        </Link>
        <h1 className="text-lg font-semibold">Admin · Historical Data Archive</h1>
      </div>

      <div className="mb-4 rounded-[10px] border border-amber-900 bg-amber-950/40 px-3.5 py-2.5 text-xs text-amber-300">
        Proactive archival only — not a retroactive backfill tool. Definedge&apos;s history endpoint stops serving a
        contract once it rolls off the live instrument master, so this can only capture currently-listed equity,
        futures, and options before they expire. Equity 1-min history is also capped at roughly 180 days back.
      </div>

      {error && <div className="mb-4 rounded-[8px] border border-red-900 bg-red-950/40 px-3 py-2 text-xs text-red-300">{error}</div>}

      <div className="mb-4 flex flex-wrap items-end gap-3 rounded-[10px] border p-3.5" style={{ background: "#12161C", borderColor: "#1F2630" }}>
        <div className="flex flex-col gap-1">
          <label className="text-[10px] text-[#8B96A5]">Stock</label>
          <select
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            className="rounded-[7px] border border-[#232C36] bg-[#0E1116] px-2.5 py-1.5 text-xs"
          >
            {stocks.map((s) => (
              <option key={s.symbol} value={s.symbol}>
                {s.symbol} (lot {s.lot_size})
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[10px] text-[#8B96A5]">Start date</label>
          <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} className="mono rounded-[7px] border border-[#232C36] bg-[#0E1116] px-2.5 py-1.5 text-xs" />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[10px] text-[#8B96A5]">End date</label>
          <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} className="mono rounded-[7px] border border-[#232C36] bg-[#0E1116] px-2.5 py-1.5 text-xs" />
        </div>
        <label className="flex items-center gap-1.5 text-xs text-[#C7CDD6]">
          <input type="checkbox" checked={includeEquity} onChange={(e) => setIncludeEquity(e.target.checked)} /> Equity
        </label>
        <label className="flex items-center gap-1.5 text-xs text-[#C7CDD6]">
          <input type="checkbox" checked={includeFutures} onChange={(e) => setIncludeFutures(e.target.checked)} /> Futures
        </label>
        <label className="flex items-center gap-1.5 text-xs text-[#C7CDD6]">
          <input type="checkbox" checked={includeOptions} onChange={(e) => setIncludeOptions(e.target.checked)} /> Options
        </label>
        <div className="flex flex-col gap-1">
          <label className="text-[10px] text-[#8B96A5]">Strike band (±)</label>
          <input
            type="number"
            min={1}
            max={40}
            value={strikeBand}
            onChange={(e) => setStrikeBand(Number(e.target.value))}
            className="mono w-16 rounded-[7px] border border-[#232C36] bg-[#0E1116] px-2.5 py-1.5 text-xs"
          />
        </div>
        <button
          onClick={handleValidate}
          disabled={validating || !symbol}
          className="rounded-[7px] border border-[#232C36] px-3.5 py-1.5 text-xs text-[#C7CDD6] disabled:opacity-50"
        >
          {validating ? "Validating…" : "Validate"}
        </button>
        <button
          onClick={handleStart}
          disabled={!validation || validation.items.length === 0}
          className="rounded-[7px] bg-[#1F8F5C] px-3.5 py-1.5 text-xs font-semibold text-white disabled:opacity-50"
        >
          Start Download
        </button>
      </div>

      {validation && (
        <div className="mb-4 rounded-[10px] border p-3.5" style={{ background: "#12161C", borderColor: "#1F2630" }}>
          <div className="mb-2 text-xs font-semibold text-[#C7CDD6]">
            VALIDATION · {validation.estimated_candle_calls} contract{validation.estimated_candle_calls === 1 ? "" : "s"} resolvable ·
            effective range {validation.clamped_start_date} → {validation.clamped_end_date}
          </div>
          {validation.warnings.map((w, i) => (
            <div key={i} className="mb-1 text-[11px] text-amber-300">
              ⚠ {w}
            </div>
          ))}
          <div className="mt-2 flex max-h-40 flex-wrap gap-1.5 overflow-y-auto">
            {validation.items.map((item) => (
              <span key={item.instrument_key} className="mono rounded-[5px] bg-[#0E1116] px-2 py-1 text-[10px] text-[#8B96A5]">
                {item.label}
              </span>
            ))}
          </div>
        </div>
      )}

      {activeJob && (
        <div className="mb-4 rounded-[10px] border p-3.5" style={{ background: "#12161C", borderColor: "#1F2630" }}>
          <div className="mb-2 flex items-center justify-between">
            <div className="text-xs font-semibold text-[#C7CDD6]">
              JOB #{activeJob.id} · {activeJob.symbol} · {activeJob.status.toUpperCase()}
            </div>
            {(activeJob.status === "pending" || activeJob.status === "running") && (
              <button onClick={handleStop} className="rounded-[6px] border border-[#3A1F22] bg-[#1F1013] px-2.5 py-1 text-[10px] text-[#F85149]">
                Stop
              </button>
            )}
          </div>
          <div className="mb-2 h-1.5 overflow-hidden rounded-full bg-[#1A2028]">
            <div
              className="h-full bg-[#3DD68C]"
              style={{ width: `${activeJob.total_items ? (activeJob.completed_items / activeJob.total_items) * 100 : 0}%` }}
            />
          </div>
          <div className="text-[11px] text-[#8B96A5]">
            {activeJob.completed_items}/{activeJob.total_items} contracts · {activeJob.total_rows.toLocaleString("en-IN")} rows stored
            {activeJob.error && <span className="ml-2 text-[#F85149]">· {activeJob.error}</span>}
          </div>
          {activeJobItems.length > 0 && (
            <div className="mt-2 flex max-h-32 flex-col gap-0.5 overflow-y-auto">
              {activeJobItems.map((item) => (
                <div key={item.instrument_key} className="mono flex justify-between text-[10px]">
                  <span className="text-[#8B96A5]">{item.instrument_key}</span>
                  <span className={item.status === "failed" ? "text-[#F85149]" : "text-[#3DD68C]"}>
                    {item.status} {item.rows ? `(${item.rows})` : ""}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {coverage.length > 0 && (
        <div className="mb-4 rounded-[10px] border p-3.5" style={{ background: "#12161C", borderColor: "#1F2630" }}>
          <div className="mb-2 text-xs font-semibold text-[#C7CDD6]">COVERAGE · {symbol}</div>
          <div className="grid grid-cols-4 gap-2 text-[11px]">
            <span className="text-[#8B96A5]">Type</span>
            <span className="text-[#8B96A5]">Rows</span>
            <span className="text-[#8B96A5]">From</span>
            <span className="text-[#8B96A5]">To</span>
            {coverage.map((c) => (
              <Fragment key={c.instrument_type}>
                <span className="mono">{c.instrument_type}</span>
                <span className="mono">{c.count.toLocaleString("en-IN")}</span>
                <span className="mono text-[#8B96A5]">{c.min_ts ? formatDateTime(c.min_ts) : "—"}</span>
                <span className="mono text-[#8B96A5]">{c.max_ts ? formatDateTime(c.max_ts) : "—"}</span>
              </Fragment>
            ))}
          </div>
        </div>
      )}

      {recentJobs.length > 0 && (
        <div className="rounded-[10px] border p-3.5" style={{ background: "#12161C", borderColor: "#1F2630" }}>
          <div className="mb-2 text-xs font-semibold text-[#C7CDD6]">RECENT JOBS</div>
          <div className="flex flex-col gap-1">
            {recentJobs.map((j) => (
              <div key={j.id} className="mono flex justify-between text-[11px]">
                <span>
                  #{j.id} {j.symbol} ({j.start_date} → {j.end_date})
                </span>
                <span className="text-[#8B96A5]">
                  {j.status} · {j.completed_items}/{j.total_items} · {j.total_rows.toLocaleString("en-IN")} rows
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
