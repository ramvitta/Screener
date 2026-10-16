"use client";

import { useEffect, useMemo, useRef } from "react";
import { createChart, darkTheme } from "openalgo-charts";
import { toOpenAlgoLine, type ChartTransformSeries } from "@/lib/charts";

type CombinedStrategyChartProps = {
  /** Combined net premium series (SELL legs contribute +premium, BUY -premium). */
  premium: ChartTransformSeries;
  /** Underlying future, drawn on a second pane so the correlation is visible. */
  future: ChartTransformSeries;
  /** Strategy breakeven (an underlying price level), computed by the screener. */
  breakeven: number;
};

/** Structured, generic time-series view of the combined spread premium. The
 * net premium is a single value over time, so it draws as a line rather than
 * Renko/P&F boxes (which are for instrument prices). Two stacked panes:
 *
 *  - Pane 0: combined net premium line + a dashed "entry net premium" line.
 *  - Pane 1: underlying future line + a dashed breakeven line.
 *
 * This works identically for every strategy type (spread / iron condor /
 * straddle / strangle) — the same two series plus the strategy's breakeven. */
export default function CombinedStrategyChart({ premium, future, breakeven }: CombinedStrategyChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  const premiumLine = useMemo(() => toOpenAlgoLine(premium.candles), [premium.candles]);
  const futureLine = useMemo(() => toOpenAlgoLine(future.candles), [future.candles]);
  // Net premium at the start of the session — the entry credit (or debit).
  const entryPremium = premium.candles[0]?.close ?? 0;

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const chart = createChart(el, {
      theme: darkTheme,
      timezone: "Asia/Kolkata",
    });

    if (premiumLine.length > 0) {
      const series = chart.addSeries("line", {
        style: { color: "#3DD68C", lineWidth: 2, priceLineVisible: false, lastValueVisible: false },
      });
      series.setData(premiumLine);
      chart.addPriceLine({
        price: entryPremium,
        color: "#8B96A5",
        dashed: true,
        label: "entry net premium",
        id: "entry-premium",
      });
    }

    if (futureLine.length > 0) {
      const series = chart.addSeries("line", {
        paneIndex: 1,
        style: { color: "#5B9BD5", lineWidth: 2, priceLineVisible: false, lastValueVisible: false },
      });
      series.setData(futureLine);
      chart.addPriceLine(
        {
          price: breakeven,
          color: "#F85149",
          dashed: true,
          label: "breakeven",
          id: "breakeven",
        },
        1,
      );
    }

    chart.fitContent();

    return () => chart.destroy();
  }, [premiumLine, futureLine, entryPremium, breakeven]);

  return (
    <div
      className="flex min-h-0 flex-col rounded-[10px] border p-3"
      style={{ background: "#12161C", borderColor: "#1F2630" }}
    >
      <div className="mb-2 flex flex-shrink-0 items-center justify-between gap-2">
        <div className="truncate text-[11.5px] font-semibold text-[#C7CDD6]" title={premium.label}>
          {premium.label}
        </div>
        <div className="flex flex-shrink-0 gap-2 text-[10px] text-[#8B96A5]">
          <span>
            net <span className="mono text-[#3DD68C]">{entryPremium.toFixed(2)}</span>
          </span>
          <span>
            BE <span className="mono text-[#F85149]">{breakeven.toLocaleString("en-IN")}</span>
          </span>
        </div>
      </div>
      <div className="min-h-0 flex-1">
        <div ref={containerRef} className="h-full w-full" />
      </div>
    </div>
  );
}
