"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { fetchDailyByte } from "@/lib/api";
import { sentimentTone, directionTone } from "@/lib/byteFormat";
import type { DailyByteDigestResponse } from "@/lib/types";

const POLL_MS = 60_000;

export default function MorningBrief() {
  const [byte, setByte] = useState<DailyByteDigestResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const b = await fetchDailyByte();
        if (!cancelled) setByte(b);
      } catch {
        // keep last known
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    const id = setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const bias = byte?.market_bias;
  const tone = sentimentTone(bias);
  const sources = byte?.sources ?? [];
  const chatLabels = Array.from(new Set(sources.map((s) => s.chat_username || s.chat_title || `#${s.group_slot}`)));

  const hasContent = !!(byte && (byte.summary || byte.top_symbols.length || byte.sources.length));

  return (
    <div className="rounded-[10px] border p-4" style={{ background: "#12161C", borderColor: "#1F2630", borderLeft: "3px solid #5B9BD5" }}>
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <span className="text-xs font-semibold text-[#C7CDD6]">MORNING DAILY BYTE</span>
          <span className="text-[10px] text-[#5B6675]">{byte?.date ?? ""}</span>
        </div>
        {!loading && (
          <span
            className="rounded-[5px] px-2 py-[3px] text-[10px] font-bold"
            style={{ background: tone.bg, color: tone.color }}
          >
            {tone.label}
          </span>
        )}
      </div>

      <Link href="/daily-byte" className="mb-2 inline-block text-[11px] font-semibold text-[#5B9BD5] hover:text-[#8FBEE8]">
        Full brief →
      </Link>

      {loading ? (
        <div className="text-xs text-[#5B6675]">Loading morning byte…</div>
      ) : !hasContent ? (
        <div className="flex items-center gap-2 text-xs text-[#8B96A5]">
          <span className="rounded-[5px] bg-[#1B2129] px-1.5 py-[3px] font-bold text-[#8B96A5]">EMPTY</span>
          <span>
            No morning byte yet — the Telegram daily-byte worker runs once a day (enable{" "}
            <span className="mono text-[#A7AFBA]">TELEGRAM_ENABLED</span> + channels and complete the one-time login).
          </span>
        </div>
      ) : (
        <>
          {byte.summary && <p className="text-sm leading-relaxed text-[#C7CDD6]">{byte.summary}</p>}

          {byte.top_symbols.length > 0 && (
            <div className="mt-3 flex flex-wrap items-center gap-1.5">
              <span className="text-[10px] text-[#8B96A5]">WATCHLIST</span>
              {byte.top_symbols.map((s, i) => (
                <span
                  key={`${s.ticker}-${i}`}
                  className="mono rounded-[5px] border px-1.5 py-[3px] text-[11px] font-bold"
                  style={{ background: "#0E1116", borderColor: "#232C36", color: directionTone(s.direction) }}
                >
                  {s.ticker}
                  {s.direction ? ` · ${s.direction.toUpperCase()}` : ""}
                </span>
              ))}
            </div>
          )}

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <span className="text-[10px] text-[#8B96A5]">SOURCES</span>
            {chatLabels.map((label, i) => (
              <span key={i} className="rounded-[5px] bg-[#1B2129] px-2 py-[3px] text-[10px] text-[#A7AFBA]">
                {label}
              </span>
            ))}
            <span className="text-[10px] text-[#5B6675]">{sources.length} message{sources.length === 1 ? "" : "s"} analysed</span>
          </div>
        </>
      )}
    </div>
  );
}
