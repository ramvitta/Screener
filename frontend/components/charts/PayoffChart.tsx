import { formatCurrency } from "@/lib/format";
import type { PayoffResponse } from "@/lib/types";

const W = 640;
const H = 260;
const PAD_TOP = 10;
const PAD_BOTTOM = 24;
const PAD_RIGHT = 60;
const PAD_LEFT = 4;

function niceTicks(min: number, max: number, count = 4): number[] {
  if (min === max) return [min];
  const step = (max - min) / count;
  return Array.from({ length: count + 1 }, (_, i) => min + step * i);
}

/** On-expiry (green/red by sign) + on-target-date (blue) P&L curves, a
 * current-spot marker, and breakeven markers — Phase 3 of the UI gap-closing
 * plan. Hand-rolled SVG to match CandlestickChart.tsx's existing style
 * rather than pulling in a charting library for one more chart type. */
export default function PayoffChart({ payoff }: { payoff: PayoffResponse }) {
  const { spot_range, at_expiry, at_target, current_spot, breakevens, max_profit, max_loss } = payoff;
  if (spot_range.length === 0) {
    return <div className="flex min-h-0 flex-1 items-center justify-center text-[11px] text-[#8B96A5]">No data.</div>;
  }

  const xMin = spot_range[0];
  const xMax = spot_range[spot_range.length - 1];
  const xRange = xMax - xMin || 1;
  const yMin = Math.min(...at_expiry, ...at_target, 0);
  const yMax = Math.max(...at_expiry, ...at_target, 0);
  const yRange = yMax - yMin || 1;

  const plotW = W - PAD_LEFT - PAD_RIGHT;
  const plotH = H - PAD_TOP - PAD_BOTTOM;
  const xFor = (price: number) => PAD_LEFT + ((price - xMin) / xRange) * plotW;
  const yFor = (pnl: number) => PAD_TOP + plotH - ((pnl - yMin) / yRange) * plotH;

  const pathFor = (series: number[]) =>
    series.map((v, i) => `${i === 0 ? "M" : "L"}${xFor(spot_range[i]).toFixed(1)},${yFor(v).toFixed(1)}`).join(" ");

  const priceTicks = niceTicks(xMin, xMax);
  const pnlTicks = niceTicks(yMin, yMax);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="mb-1 flex flex-wrap items-center justify-between gap-2 text-[11px]">
        <span className="text-[#8B96A5]">
          Current <span className="mono text-[#C7CDD6]">{current_spot.toLocaleString("en-IN")}</span>
        </span>
        <span className="text-[#8B96A5]">
          Max profit <span className="mono font-semibold text-[#3DD68C]">{formatCurrency(max_profit)}</span> · Max
          loss <span className="mono font-semibold text-[#F85149]">{formatCurrency(max_loss)}</span>
        </span>
        <span className="text-[#8B96A5]">
          Breakeven{breakevens.length > 1 ? "s" : ""}{" "}
          <span className="mono text-[#C7CDD6]">{breakevens.map((b) => b.toLocaleString("en-IN")).join(" / ") || "—"}</span>
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ width: "100%", height: "100%", minHeight: 0, display: "block" }}>
        {pnlTicks.map((p, i) => (
          <g key={i}>
            <line x1={PAD_LEFT} y1={yFor(p)} x2={PAD_LEFT + plotW} y2={yFor(p)} stroke="#1A2028" strokeWidth="1" />
            <text x={PAD_LEFT + plotW + 6} y={yFor(p) + 3} fill="#8B96A5" fontSize="9" className="mono">
              {Math.round(p).toLocaleString("en-IN")}
            </text>
          </g>
        ))}

        {/* zero line */}
        <line x1={PAD_LEFT} y1={yFor(0)} x2={PAD_LEFT + plotW} y2={yFor(0)} stroke="#2A323D" strokeWidth="1" strokeDasharray="3,3" />

        {/* current spot marker */}
        <line x1={xFor(current_spot)} y1={PAD_TOP} x2={xFor(current_spot)} y2={PAD_TOP + plotH} stroke="#F0B429" strokeWidth="1" strokeDasharray="2,2" />

        {breakevens.map((b, i) => (
          <line key={i} x1={xFor(b)} y1={PAD_TOP} x2={xFor(b)} y2={PAD_TOP + plotH} stroke="#5B9BD5" strokeWidth="1" strokeDasharray="2,2" />
        ))}

        <path d={pathFor(at_target)} fill="none" stroke="#5B9BD5" strokeWidth="1.5" />
        <path d={pathFor(at_expiry)} fill="none" stroke="#3DD68C" strokeWidth="2" />

        {priceTicks.map((p, i) => (
          <text key={i} x={xFor(p)} y={H - 6} fill="#8B96A5" fontSize="9" className="mono" textAnchor="middle">
            {Math.round(p).toLocaleString("en-IN")}
          </text>
        ))}
      </svg>
      <div className="mt-1 flex items-center gap-4 text-[9.5px] text-[#8B96A5]">
        <span className="flex items-center gap-1">
          <span className="inline-block h-[2px] w-3 bg-[#3DD68C]" /> On expiry
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-[2px] w-3 bg-[#5B9BD5]" /> On target date
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-2 w-[2px] bg-[#F0B429]" /> Current spot
        </span>
      </div>
    </div>
  );
}
