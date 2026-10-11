import { formatCurrency, formatStrategyLabel } from "@/lib/format";
import type { Position } from "@/lib/types";

export default function OpenPositionsPanel({ positions }: { positions: Position[] }) {
  const open = positions.filter((p) => p.status === "open");
  const netPnl = open.reduce((sum, p) => sum + p.unrealized_pnl, 0);

  return (
    <div
      className="flex min-h-0 flex-grow flex-col rounded-[10px] border p-3.5"
      style={{ background: "#12161C", borderColor: "#1F2630" }}
    >
      <div className="mb-2.5 flex items-center justify-between">
        <div className="text-xs font-semibold text-[#C7CDD6]">
          OPEN POSITIONS <span className="font-normal text-[#8B96A5]">· {open.length}</span>
        </div>
        <div className="text-[11px] text-[#8B96A5]">
          Net P&amp;L{" "}
          <span className={`mono font-bold ${netPnl >= 0 ? "text-[#3DD68C]" : "text-[#F85149]"}`}>
            {netPnl >= 0 ? "+" : ""}
            {formatCurrency(netPnl)}
          </span>
        </div>
      </div>

      <div className="flex flex-col gap-2 overflow-y-auto">
        {open.length === 0 && <div className="py-4 text-center text-xs text-[#8B96A5]">No open positions.</div>}
        {open.map((p) => {
          const target = p.profit_target ?? 0;
          const progress = target > 0 ? Math.min(100, Math.max(0, (p.unrealized_pnl / target) * 100)) : 0;
          const nearRisk = p.max_loss !== null && p.unrealized_pnl < 0 && Math.abs(p.unrealized_pnl) > Math.abs(p.max_loss) * 0.6;
          const barColor = nearRisk ? "#F0B429" : "#3DD68C";

          return (
            <div
              key={p.id}
              className="rounded-lg border p-2.5"
              style={{ background: "#0E1116", borderColor: "#1A2028", borderLeft: `3px solid ${barColor}` }}
            >
              <div className="flex items-center justify-between">
                <div className="text-xs font-semibold">
                  {p.symbol} {formatStrategyLabel(p.strategy_type)}{" "}
                  {p.legs.map((l) => l.strike).join("/")}
                </div>
                <div className={`mono text-[13px] font-bold ${p.unrealized_pnl >= 0 ? "text-[#3DD68C]" : "text-[#F85149]"}`}>
                  {p.unrealized_pnl >= 0 ? "+" : ""}
                  {formatCurrency(p.unrealized_pnl)}
                </div>
              </div>
              <div className="mt-1 flex justify-between text-[11px] text-[#8B96A5]">
                <span>
                  {p.legs.map((l) => `${l.action} ${l.entry_price}`).join(" · ")}
                </span>
                <span style={{ color: nearRisk ? "#F0B429" : undefined }}>
                  {nearRisk ? "Nearing max loss ⚠" : `${progress.toFixed(0)}% to profit target`}
                </span>
              </div>
              <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-[#1A2028]">
                <div className="h-full" style={{ width: `${progress}%`, background: barColor }} />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
