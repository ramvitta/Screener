"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { fetchSignalHistory } from "@/lib/api";
import { formatDate, formatDateTime, formatStrategyLabel } from "@/lib/format";
import { STRATEGY_FAMILIES } from "@/lib/strategies";
import type { SignalHistoryItem } from "@/lib/types";

const POLL_MS = 15_000;

function accentFor(type: SignalHistoryItem["strategy_type"]): string {
  const family = STRATEGY_FAMILIES.find((f) => type && f.strategyTypes.includes(type));
  return family?.accent ?? "#8B96A5";
}

/** Unified recent-signal feed: the latest accepted signals across every
 * strategy family, each linking to the chartable signal-detail view.
 * `className` lets the host (home page) make it fill leftover height so the
 * list scrolls internally instead of growing the page. */
export default function RecentSignalsFeed({ className = "" }: { className?: string }) {
  const [signals, setSignals] = useState<SignalHistoryItem[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function poll() {
      try {
        const rows = await fetchSignalHistory(30, 0, { status: "accepted", latest_only: true });
        if (!cancelled) {
          setSignals(rows);
          setError(null);
        }
      } catch {
        if (!cancelled) setError("Could not load recent signals — is the backend running?");
      }
    }
    poll();
    const interval = setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  return (
    <div
      className={`flex flex-col overflow-hidden rounded-[10px] border ${className}`}
      style={{ background: "#12161C", borderColor: "#1F2630" }}
    >
      <div className="flex shrink-0 items-center justify-between border-b px-4 py-3" style={{ borderColor: "#1F2630" }}>
        <div className="text-xs font-semibold text-[#C7CDD6]">RECENT SIGNALS</div>
        <Link href="/history" className="text-[11px] text-[#5B9BD5]">
          View all →
        </Link>
      </div>

      {error && <div className="px-4 py-3 text-sm text-[#F85149]">{error}</div>}

      {!error && signals.length === 0 && (
        <div className="px-4 py-8 text-center text-sm text-[#8B96A5]">
          No accepted signals yet — the screener runs every few minutes.
        </div>
      )}

      {!error && signals.length > 0 && (
        <div className="flex-1 divide-y overflow-y-auto" style={{ borderColor: "#1A2028" }}>
          {signals.map((s) => {
            const accent = accentFor(s.strategy_type);
            return (
              <Link
                key={s.id}
                href={`/signals/${s.id}`}
                className="flex items-center gap-3 px-4 py-2.5 transition-colors hover:bg-[#0E1116]"
                style={{ borderColor: "#1A2028" }}
              >
                <span
                  className="w-[3px] self-stretch rounded-full"
                  style={{ background: accent }}
                />
                <span
                  className="rounded-[5px] px-2 py-[3px] text-[10px] font-bold"
                  style={{ background: "#1B2129", color: accent }}
                >
                  {s.strategy_type ? formatStrategyLabel(s.strategy_type).toUpperCase() : "—"}
                </span>
                <span className="text-[13px] font-medium text-[#C7CDD6]">{s.symbol ?? "—"}</span>
                {s.expiry && <span className="text-[11px] text-[#8B96A5]">Exp {formatDate(s.expiry)}</span>}
                <span className="flex-grow truncate text-xs text-[#8B96A5]">{s.reason ?? ""}</span>
                <span className="shrink-0 text-[10px] text-[#5B6675]">
                  {s.generated_at ? formatDateTime(s.generated_at) : "—"}
                </span>
                <span className="shrink-0 text-[11px] text-[#3DD68C]">→</span>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
