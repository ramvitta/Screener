import Link from "next/link";
import { formatCurrency } from "@/lib/format";
import type { BrokerSessionInfo, IndexQuote } from "@/lib/types";

export default function TopBar({
  indices,
  availableMargin,
  session,
  connected,
  unreadAlerts,
  onReconnect,
}: {
  indices: IndexQuote[];
  availableMargin: number | null;
  session: BrokerSessionInfo | null;
  connected: boolean;
  unreadAlerts: number;
  onReconnect?: () => void;
}) {
  return (
    <div
      style={{ borderBottom: "1px solid #1F2630", background: "#12161C" }}
      className="flex flex-shrink-0 items-center gap-6 px-6 py-3"
    >
      <div className="flex items-center gap-2.5">
        <div
          className="flex h-7 w-7 items-center justify-center rounded-[7px] text-sm font-bold text-[#08110C]"
          style={{ background: "linear-gradient(135deg,#3DD68C,#1F8F5C)" }}
        >
          O
        </div>
        <div className="text-[15px] font-semibold tracking-[0.2px]">OptionScreen</div>
        <div className="rounded-[5px] bg-[#1B2129] px-2 py-0.5 text-[11px] text-[#8B96A5]">
          Read-only · No auto execution
        </div>
      </div>

      <div className="h-7 w-px bg-[#1F2630]" />

      {indices.map((q) => (
        <div key={q.symbol} className="flex items-baseline gap-2">
          <span className="text-[13px] text-[#8B96A5]">{q.symbol}</span>
          <span className="mono text-lg font-bold">{q.spot.toLocaleString("en-IN")}</span>
          <span
            className={`mono text-xs font-semibold ${q.change_pct >= 0 ? "text-[#3DD68C]" : "text-[#F85149]"}`}
          >
            {q.change_pct >= 0 ? "+" : ""}
            {q.change_pct.toFixed(2)}%
          </span>
        </div>
      ))}

      <div className="flex-grow" />

      <div className="flex items-center gap-1.5 text-xs" style={{ color: connected ? "#3DD68C" : "#F85149" }}>
        <span
          className="h-[7px] w-[7px] rounded-full"
          style={{
            background: connected ? "#3DD68C" : "#F85149",
            boxShadow: connected ? "0 0 6px #3DD68C" : "none",
          }}
        />
        {connected ? "API connected" : "API unreachable"}
      </div>
      <div className="flex items-center gap-1.5 text-xs text-[#8B96A5]">
        {session ? "Definedge · connected" : "Definedge · not logged in"}
        {!session && onReconnect && (
          <button
            onClick={onReconnect}
            className="rounded-[5px] border border-[#232C36] px-2 py-0.5 text-[10px] text-[#C7CDD6]"
            title="Force a fresh TOTP auto-login (normally happens automatically)"
          >
            Reconnect
          </button>
        )}
      </div>
      <div className="flex items-baseline gap-1.5">
        <span className="text-xs text-[#8B96A5]">Margin</span>
        <span className="mono text-sm font-semibold">
          {availableMargin !== null ? formatCurrency(availableMargin) : "—"}
        </span>
      </div>
      <div className="relative">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#8B96A5" strokeWidth="2">
          <path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
          <path d="M13.73 21a2 2 0 0 1-3.46 0" />
        </svg>
        {unreadAlerts > 0 && (
          <span className="absolute -right-1 -top-1 rounded-full bg-[#F85149] px-[5px] text-[9px] font-bold text-white">
            {unreadAlerts}
          </span>
        )}
      </div>
      <Link href="/admin" className="text-xs text-[#8B96A5]" title="Admin: historical-data archive">
        Admin
      </Link>
    </div>
  );
}
