import type { ChartSeries } from "@/lib/charts";
import { formatCurrency } from "@/lib/format";

const W = 640;
const H = 220;
const PAD_TOP = 10;
const PAD_BOTTOM = 24;
const PAD_RIGHT = 52;

function niceTicks(min: number, max: number, count = 4): number[] {
  if (min === max) return [min];
  const step = (max - min) / count;
  return Array.from({ length: count + 1 }, (_, i) => min + step * i);
}

export default function CandlestickChart({ series }: { series: ChartSeries }) {
  const candles = series.candles;
  const changeUp = (series.change ?? 0) >= 0;
  const last = candles[candles.length - 1];

  if (candles.length === 0) {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <Header series={series} changeUp={changeUp} last={last} />
        <div className="flex min-h-0 flex-1 items-center justify-center text-[11px] text-[#8B96A5]">
          No candles yet — outside market hours or awaiting the session&apos;s first bar.
        </div>
      </div>
    );
  }

  const lo = Math.min(...candles.map((c) => c.low));
  const hi = Math.max(...candles.map((c) => c.high));
  const range = hi - lo || 1;
  const plotW = W - PAD_RIGHT;
  const plotH = H - PAD_TOP - PAD_BOTTOM;
  const candleW = Math.max(1, (plotW / candles.length) * 0.6);
  const xFor = (i: number) => (i + 0.5) * (plotW / candles.length);
  const yFor = (price: number) => PAD_TOP + plotH - ((price - lo) / range) * plotH;

  const priceTicks = niceTicks(lo, hi);
  const timeIdxTicks = [0, 0.25, 0.5, 0.75, 1].map((f) => Math.min(candles.length - 1, Math.round(f * (candles.length - 1))));

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Header series={series} changeUp={changeUp} last={last} />
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        style={{ width: "100%", height: "100%", minHeight: 0, display: "block" }}
      >
        {priceTicks.map((p, i) => (
          <g key={i}>
            <line x1="0" y1={yFor(p)} x2={plotW} y2={yFor(p)} stroke="#1A2028" strokeWidth="1" />
            <text x={plotW + 6} y={yFor(p) + 3} fill="#8B96A5" fontSize="9" className="mono">
              {p.toFixed(p < 100 ? 2 : 0)}
            </text>
          </g>
        ))}

        {candles.map((c, i) => {
          const up = c.close >= c.open;
          const color = up ? "#3DD68C" : "#F85149";
          const x = xFor(i);
          const bodyTop = yFor(Math.max(c.open, c.close));
          const bodyBottom = yFor(Math.min(c.open, c.close));
          return (
            <g key={i}>
              <line x1={x} y1={yFor(c.high)} x2={x} y2={yFor(c.low)} stroke={color} strokeWidth="1" />
              <rect
                x={x - candleW / 2}
                y={bodyTop}
                width={candleW}
                height={Math.max(1, bodyBottom - bodyTop)}
                fill={color}
              />
            </g>
          );
        })}

        {timeIdxTicks.map((idx, i) => (
          <text key={i} x={xFor(idx)} y={H - 6} fill="#8B96A5" fontSize="9" className="mono" textAnchor="middle">
            {new Date(candles[idx].ts).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false })}
          </text>
        ))}
      </svg>
    </div>
  );
}

function Header({
  series,
  changeUp,
  last,
}: {
  series: ChartSeries;
  changeUp: boolean;
  last: ChartSeries["candles"][number] | undefined;
}) {
  return (
    <div className="mb-1">
      <div className="flex items-center justify-between">
        <div className="text-[11.5px] font-semibold text-[#C7CDD6]">{series.trading_symbol}</div>
        {series.change !== null && series.change_pct !== null && (
          <div className={`mono text-[11px] font-semibold ${changeUp ? "text-[#3DD68C]" : "text-[#F85149]"}`}>
            CMP: {series.ltp.toFixed(2)} ({changeUp ? "+" : ""}
            {series.change.toFixed(2)}, {changeUp ? "+" : ""}
            {series.change_pct.toFixed(2)}%)
          </div>
        )}
      </div>
      {last && (
        <div className="mono text-[9px] text-[#8B96A5]">
          Dt: {new Date(last.ts).toLocaleString("en-IN", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false })}
          {"  "}O: {last.open.toFixed(2)} H: {last.high.toFixed(2)} L: {last.low.toFixed(2)} C: {last.close.toFixed(2)}
        </div>
      )}
    </div>
  );
}

export function seriesSubtitle(series: ChartSeries): string {
  if (series.strike) return `Strike ${series.strike} · Exp ${series.expiry}`;
  return formatCurrency(series.ltp);
}
