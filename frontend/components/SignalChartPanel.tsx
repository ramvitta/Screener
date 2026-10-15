"use client";

import { useMemo, useState } from "react";
import OpenAlgoChart from "./charts/OpenAlgoChart";
import {
  CHART_TYPES,
  DEFAULT_CHART_TYPE,
  toOpenAlgoBars,
  type ChartTransformSeries,
  type ChartType,
} from "@/lib/charts";

const CHART_TYPE_LABELS: Record<ChartType, string> = {
  "point-figure": "P&F",
  renko: "Renko",
  candlestick: "Candles",
};

/** Per-contract chart panel for the signal-detail page. Renders one series
 * (future / leg / combined) from POST /charts/transforms through
 * openalgo-charts, with a per-panel Candlestick / Renko / P&F toggle (P&F
 * default). The toggle is purely client-side — it only re-selects which of the
 * backend's already-computed views to draw (box/reversal stay fetch-level). */
export default function SignalChartPanel({ series }: { series: ChartTransformSeries }) {
  const [chartType, setChartType] = useState<ChartType>(DEFAULT_CHART_TYPE);

  const view = useMemo(() => {
    switch (chartType) {
      case "candlestick":
        return { seriesType: "candlestick" as const, data: toOpenAlgoBars(series.candles) };
      case "renko":
        return { seriesType: "candlestick" as const, data: series.renko };
      case "point-figure":
        return { seriesType: "point-figure" as const, data: series.pnf };
    }
  }, [chartType, series.candles, series.renko, series.pnf]);

  return (
    <div
      className="flex min-h-0 flex-col rounded-[10px] border p-3"
      style={{ background: "#12161C", borderColor: "#1F2630" }}
    >
      <div className="mb-2 flex flex-shrink-0 items-center justify-between gap-2">
        <div className="truncate text-[11.5px] font-semibold text-[#C7CDD6]" title={series.label}>
          {series.label}
        </div>
        <div className="flex flex-shrink-0 gap-0.5 rounded-[7px] bg-[#171C23] p-[3px]">
          {CHART_TYPES.map((t) => (
            <button
              key={t}
              onClick={() => setChartType(t)}
              className="rounded-[5px] px-2 py-1 text-[10px]"
              style={{
                background: chartType === t ? "#232C36" : "transparent",
                color: chartType === t ? "#E8EAED" : "#8B96A5",
                fontWeight: chartType === t ? 600 : 400,
              }}
            >
              {CHART_TYPE_LABELS[t]}
            </button>
          ))}
        </div>
      </div>
      <div className="min-h-0 flex-1">
        <OpenAlgoChart seriesType={view.seriesType} data={view.data} />
      </div>
    </div>
  );
}
