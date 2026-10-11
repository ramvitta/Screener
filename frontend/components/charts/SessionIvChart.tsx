import { formatTime } from "@/lib/format";
import type { SessionIvPoint } from "@/lib/types";

const W = 640;
const H = 140;
const PAD_TOP = 8;
const PAD_BOTTOM = 18;
const PAD_RIGHT = 44;

/** Real intraday ATM-IV line for the current session (see
 * backend/app/api/routes/analytics.py::oi_pcr) — matches the hand-rolled SVG
 * style of CandlestickChart/PayoffChart rather than adding a chart library. */
export default function SessionIvChart({ points }: { points: SessionIvPoint[] }) {
  if (points.length < 2) {
    return <div className="flex h-full items-center justify-center text-[11px] text-[#8B96A5]">Not enough session data yet.</div>;
  }
  const ivs = points.map((p) => p.atm_iv * 100);
  const lo = Math.min(...ivs);
  const hi = Math.max(...ivs);
  const range = hi - lo || 1;
  const plotW = W - PAD_RIGHT;
  const plotH = H - PAD_TOP - PAD_BOTTOM;
  const xFor = (i: number) => (i / (points.length - 1)) * plotW;
  const yFor = (iv: number) => PAD_TOP + plotH - ((iv - lo) / range) * plotH;
  const path = ivs.map((v, i) => `${i === 0 ? "M" : "L"}${xFor(i).toFixed(1)},${yFor(v).toFixed(1)}`).join(" ");

  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ width: "100%", height: "100%" }}>
      {[lo, (lo + hi) / 2, hi].map((v, i) => (
        <g key={i}>
          <line x1="0" y1={yFor(v)} x2={plotW} y2={yFor(v)} stroke="#1A2028" strokeWidth="1" />
          <text x={plotW + 6} y={yFor(v) + 3} fill="#8B96A5" fontSize="9" className="mono">
            {v.toFixed(1)}%
          </text>
        </g>
      ))}
      <path d={path} fill="none" stroke="#5B9BD5" strokeWidth="1.5" />
      <text x="0" y={H - 4} fill="#8B96A5" fontSize="9" className="mono">
        {formatTime(points[0].ts)}
      </text>
      <text x={plotW} y={H - 4} fill="#8B96A5" fontSize="9" className="mono" textAnchor="end">
        {formatTime(points[points.length - 1].ts)}
      </text>
    </svg>
  );
}
