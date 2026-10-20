"use client";

import { useEffect, useRef } from "react";
import { createChart, darkTheme, type Bar } from "openalgo-charts";
import { DrawingController, VERTICAL_LINE } from "openalgo-charts/draw";
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
  /** Horizontal reference levels (entry / stop / targets). These are drawn as
   * primitive price lines, so they stay visible while panning and zooming. */
  priceLines?: readonly {
    id: string;
    price: number;
    color: string;
    label?: string;
    leftLabel?: string;
    badge?: string;
    dashed?: boolean;
    lineWidth?: number;
  }[];
  /** Full-height vertical markers anchored to a bar time (UTC seconds), e.g.
   * the exact moment a Telegram trade call was posted. */
  verticalLines?: readonly {
    time: number;
    color?: string;
    lineWidth?: number;
    dashed?: boolean;
  }[];
};

/** Thin React wrapper over `openalgo-charts`. Creates the chart on mount,
 * feeds the prepared bars/columns, and tears it down on unmount / when the
 * series type or data change. The library's own ResizeObserver keeps the
 * canvas sized to its container, so the host just provides a sized element. */
export default function OpenAlgoChart({ seriesType, data, priceLines, verticalLines }: OpenAlgoChartProps) {
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
      for (const line of priceLines ?? []) {
        chart.addPriceLine({
          id: line.id,
          price: line.price,
          color: line.color,
          label: line.label,
          leftLabel: line.leftLabel,
          badge: line.badge,
          dashed: line.dashed ?? true,
          lineWidth: line.lineWidth ?? 1,
        });
      }

      // The drawing tier is the cleanest way to put a full-height line at a
      // specific bar time. It is a host-authored, read-only drawing: the user
      // cannot select or edit it, so it never becomes part of a saved layout.
      let drawings: DrawingController | null = null;
      if (verticalLines?.length) {
        drawings = new DrawingController(chart);
        for (const [index, line] of verticalLines.entries()) {
          drawings.add({
            id: `vertical-time-${index}`,
            tool: VERTICAL_LINE.id,
            paneIndex: 0,
            points: [{ time: line.time, price: data[0].close }],
            style: {
              color: line.color ?? "#F0B429",
              lineWidth: line.lineWidth ?? 1.5,
              lineStyle: line.dashed === false ? "solid" : "dashed",
            },
            policy: { selectable: false, editable: false, persistent: false, listed: false },
          });
        }
      }

      chart.fitContent();
      return () => {
        drawings?.destroy();
        chart.destroy();
      };
    }

    return () => chart.destroy();
  }, [seriesType, data, priceLines, verticalLines]);

  return <div ref={containerRef} className="h-full w-full" />;
}
