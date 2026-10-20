"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import AppHeader from "./AppHeader";
import TelegramCallChart from "./TelegramCallChart";
import {
  closeTelegramCall,
  fetchTelegramCall,
  fetchTelegramCalls,
  fetchTelegramChats,
  fetchTelegramMessages,
  fetchTelegramStats,
  fetchTelegramStatus,
  triggerTelegramClassify,
  triggerTelegramSync,
  triggerTelegramTrack,
} from "@/lib/api";
import type {
  TelegramCall,
  TelegramCallDetail,
  TelegramCallStats,
  TelegramCallsPage,
  TelegramChatSummary,
  TelegramMessagesPage,
  TelegramStatusResponse,
} from "@/lib/types";

const STATUS_COLORS: Record<string, { fg: string; bg: string }> = {
  open: { fg: "#5AB0FF", bg: "rgba(90,176,255,0.12)" },
  target_hit: { fg: "#3DD68C", bg: "rgba(61,214,140,0.12)" },
  sl_hit: { fg: "#F85149", bg: "rgba(248,81,73,0.12)" },
  closed: { fg: "#C7CDD6", bg: "rgba(199,205,214,0.10)" },
  expired: { fg: "#F0B429", bg: "rgba(240,180,41,0.12)" },
};

function callStatusLabel(call: Pick<TelegramCall, "status" | "status_reason" | "tracking_status">): string {
  if (call.status === "expired" && (call.tracking_status === "contract_expired" || call.status_reason?.includes("Contract Expired"))) {
    return "Status Unknown · Contract Expired";
  }
  return call.status.replace("_", " ");
}

const CATEGORY_COLORS: Record<string, string> = {
  trade_alert: "#3DD68C",
  trade_update: "#5AB0FF",
  trade_closure: "#F0B429",
  result_calendar: "#C084FC",
  market_commentary: "#8B96A5",
  promo: "#F85149",
  other: "#5B6675",
};

function Badge({ label, color, bg }: { label: string; color: string; bg?: string }) {
  return (
    <span
      className="inline-flex whitespace-nowrap rounded-[5px] px-2 py-[3px] text-[10px] font-bold uppercase tracking-wide"
      style={{ color, background: bg ?? `${color}1F` }}
    >
      {label}
    </span>
  );
}

function fmtPrice(value?: number | null): string {
  if (value == null || Number.isNaN(value)) return "—";
  return Number(value).toLocaleString("en-IN", { maximumFractionDigits: 2 });
}

function fmtPct(value?: number | null): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function fmtDate(value?: string | null): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

function sourceUrl(msg: { chat_username?: string | null; message_id: number }): string | null {
  return msg.chat_username ? `https://t.me/${msg.chat_username}/${msg.message_id}` : null;
}

function contractLabel(call: TelegramCall): string {
  if (call.option_type && call.strike != null) {
    return `${call.symbol} ${fmtPrice(call.strike)} ${call.option_type}`;
  }
  return call.display_name || call.symbol;
}

function StatCard({ label, value, tone, hint }: { label: string; value: string | number; tone?: string; hint?: string }) {
  return (
    <div className="rounded-[10px] border px-4 py-3" style={{ background: "#12161C", borderColor: "#1F2630" }}>
      <div className="text-[10px] font-semibold uppercase tracking-wider text-[#5B6675]">{label}</div>
      <div className="mono mt-1 text-xl font-bold" style={{ color: tone ?? "#E8EAED" }}>{value}</div>
      {hint && <div className="mt-1 text-[10px] text-[#5B6675]">{hint}</div>}
    </div>
  );
}

function JobPill({ label, state, message }: { label: string; state: string; message: string }) {
  const color = state === "ok" ? "#3DD68C" : state === "running" ? "#5AB0FF" : state === "error" ? "#F85149" : state === "degraded" ? "#F0B429" : "#8B96A5";
  return (
    <div className="flex items-center gap-2 text-[10px] text-[#8B96A5]" title={message}>
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: color }} />
      <span className="font-semibold text-[#C7CDD6]">{label}</span>
      <span style={{ color }}>{state}</span>
    </div>
  );
}

export default function TelegramCallsView() {
  const [tab, setTab] = useState<"calls" | "archive">("calls");
  const [stats, setStats] = useState<TelegramCallStats | null>(null);
  const [chats, setChats] = useState<TelegramChatSummary[]>([]);
  const [status, setStatus] = useState<TelegramStatusResponse | null>(null);
  const [calls, setCalls] = useState<TelegramCallsPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [selected, setSelected] = useState<TelegramCallDetail | null>(null);
  const [selectedLoading, setSelectedLoading] = useState(false);
  const [chartCall, setChartCall] = useState<TelegramCall | null>(null);
  const wasPolling = useRef(false);

  const [statusFilter, setStatusFilter] = useState("open");
  const [symbolFilter, setSymbolFilter] = useState("");
  const [chatFilter, setChatFilter] = useState("");
  const [search, setSearch] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [limit, setLimit] = useState(100);

  const [archive, setArchive] = useState<TelegramMessagesPage | null>(null);
  const [archiveCategory, setArchiveCategory] = useState("");
  const [archiveChat, setArchiveChat] = useState("");
  const [archiveSearch, setArchiveSearch] = useState("");
  const [archiveLimit, setArchiveLimit] = useState(60);

  const [closePrice, setClosePrice] = useState("");
  const [closing, setClosing] = useState(false);

  const loadOverview = useCallback(async () => {
    try {
      const [s, c, st] = await Promise.all([fetchTelegramStats(), fetchTelegramChats(), fetchTelegramStatus()]);
      setStats(s);
      setChats(c);
      setStatus(st);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load tracker overview");
    }
  }, []);

  const loadCalls = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchTelegramCalls({
        status: statusFilter || undefined,
        symbol: symbolFilter.trim() || undefined,
        chat_id: chatFilter ? Number(chatFilter) : undefined,
        q: search.trim() || undefined,
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
        limit,
        offset: 0,
      });
      setCalls(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load trade calls");
    } finally {
      setLoading(false);
    }
  }, [statusFilter, symbolFilter, chatFilter, search, dateFrom, dateTo, limit]);

  const loadArchive = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchTelegramMessages({
        category: archiveCategory || undefined,
        chat_id: archiveChat ? Number(archiveChat) : undefined,
        q: archiveSearch.trim() || undefined,
        limit: archiveLimit,
        offset: 0,
      });
      setArchive(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load the archive");
    } finally {
      setLoading(false);
    }
  }, [archiveCategory, archiveChat, archiveSearch, archiveLimit]);

  const reloadCurrentTab = useCallback(() => {
    if (tab === "calls") void loadCalls();
    else void loadArchive();
  }, [tab, loadCalls, loadArchive]);

  useEffect(() => {
    const timer = setTimeout(() => {
      void loadOverview();
    }, 0);
    return () => clearTimeout(timer);
  }, [loadOverview]);

  useEffect(() => {
    const timer = setTimeout(() => {
      if (tab === "calls") void loadCalls();
      else void loadArchive();
    }, 0);
    return () => clearTimeout(timer);
  }, [tab, loadCalls, loadArchive]);

  useEffect(() => {
    if (!status) return;
    const running = status.sync.state === "running" || status.classify.state === "running" || status.track.state === "running";
    if (!running) {
      if (!wasPolling.current) return;
      wasPolling.current = false;
      const timer = setTimeout(() => {
        void loadOverview();
        if (tab === "calls") void loadCalls();
        else void loadArchive();
      }, 0);
      return () => clearTimeout(timer);
    }
    wasPolling.current = true;
    const timer = setInterval(() => {
      void loadOverview();
      if (tab === "calls") void loadCalls();
      else void loadArchive();
    }, 4000);
    return () => clearInterval(timer);
  }, [status, tab, loadOverview, loadCalls, loadArchive]);

  const openCall = useCallback(async (id: number) => {
    setSelectedLoading(true);
    setClosePrice("");
    try {
      setSelected(await fetchTelegramCall(id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load call detail");
    } finally {
      setSelectedLoading(false);
    }
  }, []);

  const runAction = useCallback(
    async (name: string, fn: () => Promise<unknown>) => {
      setBusy(name);
      setError(null);
      try {
        await fn();
        await loadOverview();
        // The background task starts just after the POST returns. Poll once
        // more shortly after so the sync pill flips to "running" and the
        // running-state interval above takes over. Reloading the current tab
        // too covers a sync that finishes before the interval ever sees it.
        setTimeout(() => {
          void loadOverview();
          reloadCurrentTab();
        }, 700);
        setTimeout(() => {
          void loadOverview();
          reloadCurrentTab();
        }, 2200);
      } catch (e) {
        setError(e instanceof Error ? e.message : `${name} failed`);
      } finally {
        setBusy(null);
      }
    },
    [loadOverview, reloadCurrentTab],
  );

  const handleClose = useCallback(async () => {
    if (!selected) return;
    const price = Number(closePrice);
    if (!Number.isFinite(price) || price <= 0) {
      setError("Enter a valid exit price");
      return;
    }
    setClosing(true);
    try {
      await closeTelegramCall(selected.id, price, "Manually closed from tracker page");
      const [detail] = await Promise.all([fetchTelegramCall(selected.id), loadOverview(), loadCalls()]);
      setSelected(detail);
      setClosePrice("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not close call");
    } finally {
      setClosing(false);
    }
  }, [selected, closePrice, loadOverview, loadCalls]);

  const chatOptions = useMemo(() => chats.map((c) => ({ id: c.chat_id, label: c.title || c.username || String(c.chat_id) })), [chats]);
  const rows = calls?.items ?? [];
  const archiveRows = archive?.items ?? [];

  return (
    <div className="min-h-screen bg-[#0B0E11] text-[#E8EAED]">
      <AppHeader />
      <main className="mx-auto max-w-[1400px] px-6 py-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[#3DD68C]">Telegram intelligence</div>
            <h1 className="mt-1 text-2xl font-bold">Trade Call Tracker</h1>
            <p className="mt-1 max-w-[720px] text-xs text-[#8B96A5]">
              Every BUY/SELL call from the configured Telegram groups, classified and tracked through follow-up messages or market prices.
              Source text is preserved; inferred closures are marked <span className="text-[#F0B429]">price</span>.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              onClick={() => runAction("refresh", () => triggerTelegramSync({ max_per_chat: 0, full: false, use_llm: false, classify: true }))}
              disabled={busy !== null || status?.sync.state === "running"}
              className="rounded-[7px] px-3 py-2 text-[11px] font-bold text-[#08110C] disabled:opacity-50"
              style={{ background: "linear-gradient(135deg,#3DD68C,#1F8F5C)" }}
            >
              {busy === "refresh" ? "Refreshing…" : "Refresh new"}
            </button>
            <button
              onClick={() => runAction("sync", () => triggerTelegramSync({ max_per_chat: 0, full: true, use_llm: false, classify: true }))}
              disabled={busy !== null || status?.sync.state === "running"}
              className="rounded-[7px] border px-3 py-2 text-[11px] font-semibold text-[#C7CDD6] disabled:opacity-50"
              style={{ borderColor: "#2E3844", background: "#12161C" }}
            >
              {busy === "sync" ? "Backfilling…" : "Full history"}
            </button>
            <button
              onClick={() => runAction("classify", () => triggerTelegramClassify({ limit: 500, use_llm: false, only_unclassified: true }))}
              disabled={busy !== null}
              className="rounded-[7px] border px-3 py-2 text-[11px] font-semibold text-[#C7CDD6] disabled:opacity-50"
              style={{ borderColor: "#2E3844", background: "#12161C" }}
            >
              {busy === "classify" ? "Classifying…" : "Classify backlog"}
            </button>
            <button
              onClick={() => runAction("track", () => triggerTelegramTrack(50))}
              disabled={busy !== null}
              className="rounded-[7px] border px-3 py-2 text-[11px] font-semibold text-[#C7CDD6] disabled:opacity-50"
              style={{ borderColor: "#2E3844", background: "#12161C" }}
            >
              {busy === "track" ? "Tracking…" : "Mark to market"}
            </button>
          </div>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-1">
          <JobPill label="Sync" state={status?.sync.state ?? "starting"} message={status?.sync.message ?? ""} />
          <JobPill label="Classify" state={status?.classify.state ?? "starting"} message={status?.classify.message ?? ""} />
          <JobPill label="Price tracking" state={status?.track.state ?? "starting"} message={status?.track.message ?? ""} />
          {stats?.last_sync_at && <span className="text-[10px] text-[#5B6675]">last sync {fmtDate(stats.last_sync_at)}</span>}
        </div>

        {error && (
          <div className="mt-3 rounded-[8px] border px-3 py-2 text-xs" style={{ borderColor: "#5B2530", background: "#1B1114", color: "#F85149" }}>
            {error}
          </div>
        )}

        <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
          <StatCard label="Calls" value={stats?.total_calls ?? 0} />
          <StatCard label="Open" value={stats?.open ?? 0} tone="#5AB0FF" />
          <StatCard label="Target hit" value={stats?.target_hit ?? 0} tone="#3DD68C" />
          <StatCard label="SL hit" value={stats?.sl_hit ?? 0} tone="#F85149" />
          <StatCard label="Win rate" value={stats?.win_rate != null ? `${stats.win_rate.toFixed(1)}%` : "—"} tone="#3DD68C" />
          <StatCard label="Avg closed P&L" value={stats?.avg_realized_pct != null ? fmtPct(stats.avg_realized_pct) : "—"} tone={(stats?.avg_realized_pct ?? 0) >= 0 ? "#3DD68C" : "#F85149"} />
          <StatCard label="Messages" value={stats?.total_messages ?? 0} hint={`${stats?.classified_messages ?? 0} classified`} />
          <StatCard label="Unresolved" value={stats?.unresolved ?? 0} tone={(stats?.unresolved ?? 0) > 0 ? "#F0B429" : "#8B96A5"} hint="contracts not in master" />
        </div>

        <div className="mt-5 flex items-center gap-1 border-b" style={{ borderColor: "#1F2630" }}>
          {(["calls", "archive"] as const).map((key) => (
            <button
              key={key}
              onClick={() => setTab(key)}
              className="border-b-2 px-3 py-2 text-xs font-semibold"
              style={{ borderColor: tab === key ? "#3DD68C" : "transparent", color: tab === key ? "#E8EAED" : "#8B96A5" }}
            >
              {key === "calls" ? `Calls${calls ? ` (${calls.total})` : ""}` : `Raw archive${archive ? ` (${archive.total})` : ""}`}
            </button>
          ))}
        </div>

        {tab === "calls" ? (
          <>
            <div className="mt-3 flex flex-wrap items-end gap-2">
              <label className="text-[10px] text-[#5B6675]">STATUS
                <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} className="mt-1 block rounded-[6px] border bg-[#0E1116] px-2 py-1.5 text-xs text-[#E8EAED]" style={{ borderColor: "#232C36" }}>
                  <option value="">All</option>
                  <option value="open">Open</option>
                  <option value="target_hit">Target hit</option>
                  <option value="sl_hit">SL hit</option>
                  <option value="closed">Closed</option>
                  <option value="expired">Expired</option>
                </select>
              </label>
              <label className="text-[10px] text-[#5B6675]">CHAT
                <select value={chatFilter} onChange={(e) => setChatFilter(e.target.value)} className="mt-1 block max-w-[220px] rounded-[6px] border bg-[#0E1116] px-2 py-1.5 text-xs text-[#E8EAED]" style={{ borderColor: "#232C36" }}>
                  <option value="">All chats</option>
                  {chatOptions.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
                </select>
              </label>
              <label className="text-[10px] text-[#5B6675]">SYMBOL
                <input value={symbolFilter} onChange={(e) => setSymbolFilter(e.target.value)} placeholder="NIFTY" className="mt-1 block w-28 rounded-[6px] border bg-[#0E1116] px-2 py-1.5 text-xs text-[#E8EAED] outline-none" style={{ borderColor: "#232C36" }} />
              </label>
              <label className="text-[10px] text-[#5B6675]">SEARCH
                <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="message text or symbol" className="mt-1 block w-52 rounded-[6px] border bg-[#0E1116] px-2 py-1.5 text-xs text-[#E8EAED] outline-none" style={{ borderColor: "#232C36" }} />
              </label>
              <label className="text-[10px] text-[#5B6675]">FROM
                <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="mt-1 block rounded-[6px] border bg-[#0E1116] px-2 py-1.5 text-xs text-[#E8EAED]" style={{ borderColor: "#232C36" }} />
              </label>
              <label className="text-[10px] text-[#5B6675]">TO
                <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="mt-1 block rounded-[6px] border bg-[#0E1116] px-2 py-1.5 text-xs text-[#E8EAED]" style={{ borderColor: "#232C36" }} />
              </label>
              <label className="text-[10px] text-[#5B6675]">LIMIT
                <select value={limit} onChange={(e) => setLimit(Number(e.target.value))} className="mt-1 block rounded-[6px] border bg-[#0E1116] px-2 py-1.5 text-xs text-[#E8EAED]" style={{ borderColor: "#232C36" }}>
                  {[50, 100, 200, 500].map((n) => <option key={n} value={n}>{n}</option>)}
                </select>
              </label>
            </div>

            <div className="mt-3 overflow-hidden rounded-[10px] border" style={{ borderColor: "#1F2630" }}>
              <table className="w-full border-collapse text-left text-xs">
                <thead>
                  <tr className="text-[10px] uppercase tracking-wider text-[#5B6675]" style={{ background: "#12161C" }}>
                    <th className="px-3 py-2 font-semibold">Posted</th>
                    <th className="px-3 py-2 font-semibold">Chat</th>
                    <th className="px-3 py-2 font-semibold">Call</th>
                    <th className="px-3 py-2 font-semibold">Entry</th>
                    <th className="px-3 py-2 font-semibold">SL</th>
                    <th className="px-3 py-2 font-semibold">Targets</th>
                    <th className="px-3 py-2 font-semibold">Status</th>
                    <th className="px-3 py-2 text-right font-semibold">Exit / LTP</th>
                    <th className="px-3 py-2 text-right font-semibold">P&L</th>
                    <th className="px-3 py-2 font-semibold">Source</th>
                  </tr>
                </thead>
                <tbody>
                  {loading && !calls ? (
                    <tr><td colSpan={10} className="px-3 py-8 text-center text-[#8B96A5]">Loading calls…</td></tr>
                  ) : rows.length === 0 ? (
                    <tr><td colSpan={10} className="px-3 py-8 text-center text-[#8B96A5]">No calls match these filters. Run a full-history sync above.</td></tr>
                  ) : rows.map((call) => {
                    const tone = STATUS_COLORS[call.status] ?? STATUS_COLORS.closed;
                    const pnl = call.realized_pct ?? (call.last_price != null && call.entry_price ? ((call.direction === "short" ? call.entry_price - call.last_price : call.last_price - call.entry_price) / call.entry_price) * 100 : null);
                    return (
                      <tr
                        key={call.id}
                        onClick={() => openCall(call.id)}
                        className="cursor-pointer border-t align-top hover:bg-[#151A21]"
                        style={{ borderColor: "#1A2028", background: selected?.id === call.id ? "#151A21" : undefined }}
                      >
                        <td className="whitespace-nowrap px-3 py-2.5 text-[#8B96A5]">{fmtDate(call.posted_at)}</td>
                        <td className="max-w-[150px] px-3 py-2.5 text-[#C7CDD6]">{call.chat_title || call.chat_username || call.chat_id}</td>
                        <td className="px-3 py-2.5">
                          <div className="flex items-center gap-1.5">
                            <Badge label={call.action} color={call.action === "BUY" ? "#3DD68C" : "#F85149"} />
                            <span className="font-semibold text-[#E8EAED]">{contractLabel(call)}</span>
                          </div>
                          <div className="mt-0.5 text-[10px] text-[#5B6675]">{call.segment.replaceAll("_", " ")} {call.horizon ? `· ${call.horizon}` : ""}</div>
                        </td>
                        <td className="mono whitespace-nowrap px-3 py-2.5 text-[#C7CDD6]">
                          {call.entry_low != null && call.entry_high != null && call.entry_low !== call.entry_high ? `${fmtPrice(call.entry_low)}–${fmtPrice(call.entry_high)}` : fmtPrice(call.entry_price)}
                        </td>
                        <td className="mono whitespace-nowrap px-3 py-2.5 text-[#F85149]">{fmtPrice(call.stop_loss)}</td>
                        <td className="mono whitespace-nowrap px-3 py-2.5 text-[#F0B429]">
                          {[call.target_1, call.target_2, call.target_3].filter((v) => v != null).map(fmtPrice).join(" / ") || "—"}
                        </td>
                        <td className="whitespace-nowrap px-3 py-2.5">
                          <Badge label={callStatusLabel(call)} color={tone.fg} bg={tone.bg} />
                          {call.status_source && <div className="mt-1 text-[9px] text-[#5B6675]">via {call.status_source}</div>}
                        </td>
                        <td className="mono whitespace-nowrap px-3 py-2.5 text-right text-[#C7CDD6]">{fmtPrice(call.exit_price ?? call.last_price)}</td>
                        <td className="mono whitespace-nowrap px-3 py-2.5 text-right font-semibold" style={{ color: pnl == null ? "#5B6675" : pnl >= 0 ? "#3DD68C" : "#F85149" }}>{fmtPct(pnl)}</td>
                        <td className="px-3 py-2.5">
                          <div className="flex items-center gap-2 whitespace-nowrap">
                            {sourceUrl(call) ? (
                              <a href={sourceUrl(call)!} target="_blank" rel="noreferrer" className="text-[#5AB0FF] hover:underline" onClick={(e) => e.stopPropagation()}>source</a>
                            ) : (
                              <span className="text-[#5B6675]">—</span>
                            )}
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                setChartCall(call);
                              }}
                              className="text-[#3DD68C] hover:underline"
                              title="Open 5-minute chart with entry, SL and targets"
                            >
                              5m chart
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {selectedLoading && <div className="mt-3 text-xs text-[#8B96A5]">Loading call detail…</div>}
            {selected && !selectedLoading && (
              <div className="mt-4 rounded-[10px] border" style={{ background: "#12161C", borderColor: "#1F2630" }}>
                <div className="flex items-center justify-between border-b px-4 py-3" style={{ borderColor: "#1F2630" }}>
                  <div>
                    <div className="flex items-center gap-2">
                      <Badge label={selected.action} color={selected.action === "BUY" ? "#3DD68C" : "#F85149"} />
                      <span className="text-sm font-bold">{contractLabel(selected)}</span>
                      <Badge label={callStatusLabel(selected)} color={(STATUS_COLORS[selected.status] ?? STATUS_COLORS.closed).fg} />
                    </div>
                    <div className="mt-1 text-[10px] text-[#5B6675]">{selected.chat_title || selected.chat_username} · {fmtDate(selected.posted_at)} · {selected.method} · confidence {selected.confidence ?? "—"}</div>
                  </div>
                  <button onClick={() => setChartCall(selected)} className="text-xs text-[#3DD68C] hover:underline">5m chart</button>
                  <button onClick={() => setSelected(null)} className="ml-3 text-xs text-[#8B96A5] hover:text-[#E8EAED]">Close ✕</button>
                </div>
                <div className="grid gap-4 p-4 lg:grid-cols-[1.2fr_1fr]">
                  <div>
                    <div className="text-[10px] font-semibold uppercase tracking-wider text-[#5B6675]">Source message</div>
                    <div className="mt-2 whitespace-pre-wrap rounded-[8px] border p-3 text-xs leading-5 text-[#C7CDD6]" style={{ background: "#0E1116", borderColor: "#1A2028" }}>
                      {selected.source_text || "—"}
                    </div>
                    {(selected.tracking_message || selected.status_reason) && (
                      <div className="mt-2 text-[10px] text-[#8B96A5]">Tracker: {selected.tracking_message || selected.status_reason}</div>
                    )}
                    {selected.status === "open" && (
                      <div className="mt-3 flex items-end gap-2">
                        <label className="text-[10px] text-[#5B6675]">MANUAL EXIT PRICE
                          <input value={closePrice} onChange={(e) => setClosePrice(e.target.value)} inputMode="decimal" placeholder={fmtPrice(selected.last_price ?? selected.entry_price)} className="mono mt-1 block w-40 rounded-[6px] border bg-[#0E1116] px-2 py-1.5 text-xs text-[#E8EAED] outline-none" style={{ borderColor: "#232C36" }} />
                        </label>
                        <button onClick={handleClose} disabled={closing} className="rounded-[6px] border px-3 py-1.5 text-[11px] font-semibold text-[#F0B429] disabled:opacity-50" style={{ borderColor: "#3A3320", background: "#17140D" }}>{closing ? "Closing…" : "Close call"}</button>
                      </div>
                    )}
                  </div>
                  <div>
                    <div className="text-[10px] font-semibold uppercase tracking-wider text-[#5B6675]">Follow-up events</div>
                    <div className="mt-2 space-y-2">
                      {selected.events.length === 0 && <div className="text-[11px] text-[#5B6675]">No channel follow-up matched yet.</div>}
                      {selected.events.map((event) => (
                        <div key={event.id} className="rounded-[7px] border p-2.5" style={{ background: "#0E1116", borderColor: "#1A2028" }}>
                          <div className="flex items-center justify-between gap-2">
                            <Badge label={event.event_type.replace("_", " ")} color={event.event_type.includes("sl") ? "#F85149" : event.event_type.includes("target") || event.event_type === "closed" ? "#3DD68C" : "#5AB0FF"} />
                            <span className="text-[9px] text-[#5B6675]">{fmtDate(event.posted_at)}</span>
                          </div>
                          <div className="mono mt-1 text-[11px] text-[#C7CDD6]">
                            {event.price != null ? `price ${fmtPrice(event.price)}` : event.target_value != null ? `target ${fmtPrice(event.target_value)}` : "—"}
                            {event.realized_pct != null ? ` · ${fmtPct(event.realized_pct)}` : ""}
                          </div>
                          {event.note && <div className="mt-1 line-clamp-2 text-[10px] text-[#8B96A5]">{event.note}</div>}
                        </div>
                      ))}
                    </div>
                    {selected.thread.length > 0 && (
                      <>
                        <div className="mt-4 text-[10px] font-semibold uppercase tracking-wider text-[#5B6675]">Related thread ({selected.thread.length})</div>
                        <div className="mt-2 max-h-40 space-y-1 overflow-auto">
                          {selected.thread.map((msg) => (
                            <div key={msg.id} className="rounded-[6px] px-2 py-1.5 text-[10px]" style={{ background: "#0E1116" }}>
                              <span className="text-[#5B6675]">{fmtDate(msg.posted_at)}</span>{" "}
                              <span className="text-[#C7CDD6]">{msg.text.slice(0, 220)}{msg.text.length > 220 ? "…" : ""}</span>
                            </div>
                          ))}
                        </div>
                      </>
                    )}
                  </div>
                </div>
              </div>
            )}
          </>
        ) : (
          <>
            <div className="mt-3 flex flex-wrap items-end gap-2">
              <label className="text-[10px] text-[#5B6675]">CHAT
                <select value={archiveChat} onChange={(e) => setArchiveChat(e.target.value)} className="mt-1 block max-w-[220px] rounded-[6px] border bg-[#0E1116] px-2 py-1.5 text-xs text-[#E8EAED]" style={{ borderColor: "#232C36" }}>
                  <option value="">All chats</option>
                  {chatOptions.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
                </select>
              </label>
              <label className="text-[10px] text-[#5B6675]">CATEGORY
                <select value={archiveCategory} onChange={(e) => setArchiveCategory(e.target.value)} className="mt-1 block rounded-[6px] border bg-[#0E1116] px-2 py-1.5 text-xs text-[#E8EAED]" style={{ borderColor: "#232C36" }}>
                  <option value="">All categories</option>
                  {Object.keys(CATEGORY_COLORS).map((c) => <option key={c} value={c}>{c.replace("_", " ")}</option>)}
                </select>
              </label>
              <label className="text-[10px] text-[#5B6675]">SEARCH
                <input value={archiveSearch} onChange={(e) => setArchiveSearch(e.target.value)} placeholder="text, symbol, keyword" className="mt-1 block w-64 rounded-[6px] border bg-[#0E1116] px-2 py-1.5 text-xs text-[#E8EAED] outline-none" style={{ borderColor: "#232C36" }} />
              </label>
              <label className="text-[10px] text-[#5B6675]">LIMIT
                <select value={archiveLimit} onChange={(e) => setArchiveLimit(Number(e.target.value))} className="mt-1 block rounded-[6px] border bg-[#0E1116] px-2 py-1.5 text-xs text-[#E8EAED]" style={{ borderColor: "#232C36" }}>
                  {[30, 60, 120, 300].map((n) => <option key={n} value={n}>{n}</option>)}
                </select>
              </label>
            </div>
            <div className="mt-3 space-y-2">
              {loading && !archive ? (
                <div className="py-8 text-center text-xs text-[#8B96A5]">Loading archive…</div>
              ) : archiveRows.length === 0 ? (
                <div className="py-8 text-center text-xs text-[#8B96A5]">No archived messages match these filters.</div>
              ) : archiveRows.map((msg) => {
                const url = sourceUrl(msg);
                const category = msg.category || "other";
                return (
                  <div key={msg.id} className="rounded-[9px] border p-3" style={{ background: "#12161C", borderColor: "#1F2630" }}>
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge label={category.replace("_", " ")} color={CATEGORY_COLORS[category] ?? CATEGORY_COLORS.other} />
                      {msg.call_count > 0 && <Badge label={`${msg.call_count} call${msg.call_count === 1 ? "" : "s"}`} color="#3DD68C" />}
                      <span className="text-[10px] text-[#8B96A5]">{msg.chat_title || msg.chat_username || msg.chat_id}</span>
                      <span className="text-[10px] text-[#5B6675]">{fmtDate(msg.posted_at)}</span>
                      {msg.sender_name && <span className="text-[10px] text-[#5B6675]">· {msg.sender_name}</span>}
                      <span className="flex-grow" />
                      {url && <a href={url} target="_blank" rel="noreferrer" className="text-[10px] text-[#5AB0FF] hover:underline">Open in Telegram ↗</a>}
                    </div>
                    <div className="mt-2 whitespace-pre-wrap text-xs leading-5 text-[#C7CDD6]">{msg.text.length > 1400 ? `${msg.text.slice(0, 1400)}…` : msg.text}</div>
                  </div>
                );
              })}
            </div>
          </>
        )}
      </main>
      {chartCall && <TelegramCallChart key={chartCall.id} call={chartCall} onClose={() => setChartCall(null)} />}
    </div>
  );
}
