"use client";

import { useEffect, useRef } from "react";
import { createChart, darkTheme, type Bar } from "openalgo-charts";
// Registers the Family-B `point-figure` (and `kagi`) renderers as a side effect.
import "openalgo-charts/transform";

type OpenAlgoChartProps = {
  /** openalgo-charts series type. Candlestick and Renko both render as
   * `candlestick` (Renko bricks are candle-shaped); P&F renders as
   * `point-figure`. */
  seriesType: "candlestick" | "point-figure";
  /** Bars (candlestick/Renko) or P&F columns, already in openalgo format.
   * P&F columns are structurally `Bar` + `boxSize`/`boxes`, which the
   * point-figure renderer reads straight off the data. */
  data: readonly Bar[];
};

/** Thin React wrapper over `openalgo-charts`. Creates the chart on mount,
 * feeds the prepared bars/columns, and tears it down on unmount / when the
 * series type or data change. The library's own ResizeObserver keeps the
 * canvas sized to its container, so the host just provides a sized element. */
export default function OpenAlgoChart({ seriesType, data }: OpenAlgoChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const chart = createChart(el, {
      theme: darkTheme,
      timezone: "Asia/Kolkata",
    });

    if (data.length > 0) {
      const series = chart.addSeries(seriesType, {
        style: { priceLineVisible: false, lastValueVisible: false },
      });
      series.setData(data);
      chart.fitContent();
    }

    return () => chart.destroy();
  }, [seriesType, data]);

  return <div ref={containerRef} className="h-full w-full" />;
}
