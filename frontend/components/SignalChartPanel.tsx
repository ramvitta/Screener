import CandlestickChart from "./charts/CandlestickChart";
import type { ChartSeries } from "@/lib/charts";

/** Per-contract chart panel for the signal-detail page.
 *
 * Workstream 2 keeps this a thin wrapper over the hand-rolled
 * CandlestickChart so `ContractDetailView` can render an arbitrary number of
 * legs dynamically (future + N legs + combined). Workstream 3 swaps the
 * internals for the openalgo-charts renderers and adds the per-panel
 * Candlestick / Renko / P&F toggle (P&F default) + box/reversal controls —
 * without touching the call sites in `ContractDetailView`. */
export default function SignalChartPanel({ series }: { series: ChartSeries }) {
  return (
    <div className="flex min-h-0 flex-col rounded-[10px] border p-3" style={{ background: "#12161C", borderColor: "#1F2630" }}>
      <CandlestickChart series={series} />
    </div>
  );
}
