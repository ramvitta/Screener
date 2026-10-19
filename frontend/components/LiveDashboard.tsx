"use client";

import { useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import TopBar from "./TopBar";
import ExpiryStrip, { type ExpiryChip } from "./ExpiryStrip";
import FilterBar from "./FilterBar";
import SignalFeed from "./SignalFeed";
import DecisionParameters from "./DecisionParameters";
import OpenPositionsPanel from "./OpenPositionsPanel";
import PositionsPnlPanel from "./PositionsPnlPanel";
import OptionChainPanel from "./OptionChainPanel";
import PayoffPanel from "./PayoffPanel";
import MarketAnalyticsPanel from "./MarketAnalyticsPanel";
import AlertsList from "./AlertsList";
import DataSourceBanner from "./DataSourceBanner";
import {
  fetchAlerts,
  fetchAvailableMargin,
  fetchBackfillStatus,
  fetchBrokerSession,
  fetchIndexQuotes,
  fetchPositions,
  fetchScreenerSignals,
  fetchScreenerStatus,
  refreshPositionLtp,
  triggerRelogin,
} from "@/lib/api";
import type { Alert, BackfillStatus, BrokerSessionInfo, IndexQuote, Position, ScreenerSignal, ScreenerStatus, StrategyType } from "@/lib/types";
import { isStrategyType } from "@/lib/strategies";

const POLL_MS = 5000;
const LTP_REFRESH_MS = 5000;

type Tab = "signals" | "positions" | "chain" | "analytics" | "history";

const TABS: { key: Tab; label: string }[] = [
  { key: "signals", label: "Signals" },
  { key: "positions", label: "Positions & PNL" },
  { key: "chain", label: "Chain & Entry" },
  { key: "analytics", label: "Analytics" },
  { key: "history", label: "History & Backtest" },
];

/** Dashboard shell backed by the FastAPI backend (see ../backend). Polls REST
 * endpoints on an interval as a stand-in for /ws/quotes and /ws/alerts. No
 * mock-data fallback: every panel shows real backend state, including empty
 * ("No signals yet", "No open positions.") when the backend has nothing or
 * is unreachable — a stale hardcoded fallback previously masked real bugs
 * (see the 29-Sep expired-contract issue) by looking like live data. */
export default function LiveDashboard() {
  const searchParams = useSearchParams();
  const strategyParam = searchParams.get("strategy");
  const [connected, setConnected] = useState(false);
  const [signals, setSignals] = useState<ScreenerSignal[]>([]);
  const [positions, setPositions] = useState<Position[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [indices, setIndices] = useState<IndexQuote[]>([]);
  const [, setBackfill] = useState<BackfillStatus[]>([]);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const [brokerSession, setBrokerSession] = useState<BrokerSessionInfo | null>(null);
  const [availableMargin, setAvailableMargin] = useState<number | null>(null);
  const [screenerStatus, setScreenerStatus] = useState<ScreenerStatus | null>(null);

  const [strategyFilter, setStrategyFilter] = useState<StrategyType | "all">(
    isStrategyType(strategyParam) ? strategyParam : "all",
  );
  const [expiryFilter, setExpiryFilter] = useState<ExpiryChip | null>(null);
  const [tab, setTab] = useState<Tab>("signals");

  async function handleReconnect() {
    try {
      await triggerRelogin();
      const session = await fetchBrokerSession();
      setBrokerSession(session);
    } catch {
      // surfaced implicitly — the "not logged in" state simply persists
    }
  }

  async function reloadPositions() {
    try {
      setPositions(await fetchPositions());
    } catch {
      // keep showing whatever we already have
    }
  }

  // Best-effort live LTP refresh for open legs (no-ops quietly without an
  // active Definedge session — see positions.py::refresh_ltp). Only runs
  // while the Positions & PNL tab is visible.
  useEffect(() => {
    if (tab !== "positions") return;
    let cancelled = false;
    async function tick() {
      try {
        const p = await refreshPositionLtp();
        if (!cancelled) setPositions(p);
      } catch {
        // ignore — falls back to last known LTP
      }
    }
    tick();
    const interval = setInterval(tick, LTP_REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [tab]);

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      try {
        const [s, p, a, b] = await Promise.all([
          fetchScreenerSignals(),
          fetchPositions(),
          fetchAlerts(),
          fetchBackfillStatus(),
        ]);
        if (cancelled) return;
        setConnected(true);
        setSignals(s);
        setPositions(p);
        setAlerts(a);
        setBackfill(b);
        setUpdatedAt(new Date().toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }));
      } catch {
        if (!cancelled) setConnected(false);
      }

      // Broker session/margin/indices depend on being logged in with
      // Definedge — polled separately so an unauthenticated session doesn't
      // flip the API-connected indicator off.
      try {
        const session = await fetchBrokerSession();
        if (!cancelled) setBrokerSession(session);
      } catch {
        if (!cancelled) setBrokerSession(null);
      }
      try {
        const status = await fetchScreenerStatus();
        if (!cancelled) setScreenerStatus(status);
      } catch {
        if (!cancelled) setScreenerStatus(null);
      }
      try {
        const margin = await fetchAvailableMargin();
        if (!cancelled) setAvailableMargin(margin);
      } catch {
        if (!cancelled) setAvailableMargin(null);
      }
      try {
        const idx = await fetchIndexQuotes();
        if (!cancelled) setIndices(idx);
      } catch {
        if (!cancelled) setIndices([]);
      }
    }

    poll();
    const interval = setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  // Belt-and-suspenders: never render a signal whose expiry has already
  // passed, in case a future screener bug ever produces one again (this is
  // exactly the class of bug — spreads/condors on an already-expired
  // contract — a user previously hit).
  const activeSignals = useMemo(() => {
    const todayIso = new Date().toISOString().slice(0, 10);
    return signals.filter((s) => s.expiry >= todayIso);
  }, [signals]);

  const expiryChips = useMemo<ExpiryChip[]>(() => {
    const seen = new Set<string>();
    const chips: ExpiryChip[] = [];
    for (const s of activeSignals) {
      const key = `${s.symbol}|${s.expiry}`;
      if (!seen.has(key)) {
        seen.add(key);
        chips.push({ symbol: s.symbol, expiry: s.expiry });
      }
    }
    return chips;
  }, [activeSignals]);

  const filteredSignals = useMemo(() => {
    return activeSignals.filter((s) => {
      if (strategyFilter !== "all" && s.strategy_type !== strategyFilter) return false;
      if (expiryFilter && (s.symbol !== expiryFilter.symbol || s.expiry !== expiryFilter.expiry)) return false;
      return true;
    });
  }, [activeSignals, strategyFilter, expiryFilter]);

  const unreadAlerts = alerts.filter((a) => !a.is_read).length;

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-[#0B0E11] text-[#E8EAED]">
      <TopBar
        indices={indices}
        availableMargin={availableMargin}
        session={brokerSession}
        connected={connected}
        unreadAlerts={unreadAlerts}
        onReconnect={handleReconnect}
      />
      <DataSourceBanner status={screenerStatus} />
      <div
        style={{ borderBottom: "1px solid #1A2028", background: "#0E1116" }}
        className="flex flex-shrink-0 items-center gap-1 px-6 py-1.5"
      >
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className="rounded-[7px] px-3 py-1.5 text-xs"
            style={{
              background: tab === t.key ? "#232C36" : "transparent",
              color: tab === t.key ? "#E8EAED" : "#8B96A5",
              fontWeight: tab === t.key ? 600 : 400,
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "signals" && (
        <>
          <ExpiryStrip chips={expiryChips} selected={expiryFilter} onSelect={setExpiryFilter} />
          <FilterBar strategy={strategyFilter} onStrategyChange={setStrategyFilter} />
        </>
      )}

      {!connected && (
        <div className="border-b border-amber-900 bg-amber-950/60 px-6 py-1.5 text-center text-xs text-amber-300">
          Backend unreachable — retrying every {POLL_MS / 1000}s.
        </div>
      )}

      {tab === "signals" && (
        <div className="flex min-h-0 flex-grow gap-4 overflow-hidden px-6 py-3.5">
          <SignalFeed signals={filteredSignals} updatedAt={updatedAt} />

          <div className="flex min-h-0 flex-grow flex-col gap-3 overflow-y-auto">
            <DecisionParameters />
            <OpenPositionsPanel positions={positions} />
            <AlertsList alerts={alerts} />
          </div>
        </div>
      )}

      {tab === "positions" && (
        <div className="min-h-0 flex-grow overflow-y-auto px-6 py-3.5">
          <PositionsPnlPanel positions={positions} onChanged={reloadPositions} />
        </div>
      )}

      {tab === "chain" && (
        <div className="flex min-h-0 flex-grow flex-col px-6 py-3.5">
          <OptionChainPanel />
        </div>
      )}

      {tab === "analytics" && (
        <div className="flex min-h-0 flex-grow flex-col gap-3 overflow-y-auto px-6 py-3.5">
          <div style={{ minHeight: 340 }} className="flex flex-col">
            <PayoffPanel signals={signals} positions={positions} />
          </div>
          <MarketAnalyticsPanel />
        </div>
      )}

      {tab === "history" && (
        <div className="flex min-h-0 flex-grow items-center justify-center px-6 py-3.5 text-sm text-[#8B96A5]">
          Expiry-day performance & strategy backtests — coming in a later build phase.
        </div>
      )}
    </div>
  );
}
