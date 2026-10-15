import { describe, expect, it } from "vitest";
import {
  BOX_PRESETS,
  CHART_TYPES,
  DEFAULT_BOX_PCT,
  DEFAULT_CHART_TYPE,
  DEFAULT_COMBINED_BOX_PCT,
  DEFAULT_REVERSAL,
  toOpenAlgoBars,
  toUtcSeconds,
} from "../lib/charts";

describe("toUtcSeconds", () => {
  it("converts a naive IST ISO string to UTC seconds", () => {
    // 09:15 IST == 03:45 UTC — matches the backend to_utc_seconds test value.
    expect(toUtcSeconds("2024-01-01T09:15:00")).toBe(1704080700);
  });

  it("handles the session boundaries", () => {
    // 00:00 IST == 18:30 UTC (previous day).
    expect(toUtcSeconds("2024-01-01T00:00:00")).toBe(1704047400);
    // 15:30 IST == 10:00 UTC (market close).
    expect(toUtcSeconds("2024-01-01T15:30:00")).toBe(1704103200);
  });

  it("never depends on the host timezone (IST is a fixed +5:30 offset)", () => {
    // The offset is constant, so consecutive wall-clock seconds stay 1s apart.
    expect(toUtcSeconds("2024-01-01T09:15:01") - toUtcSeconds("2024-01-01T09:15:00")).toBe(1);
  });
});

describe("toOpenAlgoBars", () => {
  it("maps candles to openalgo bars and converts ts -> time", () => {
    const bars = toOpenAlgoBars([
      { ts: "2024-01-01T09:15:00", open: 100, high: 105, low: 99, close: 104, volume: 1000, oi: 50 },
      { ts: "2024-01-01T09:16:00", open: 104, high: 106, low: 103, close: 105, volume: 800, oi: 0 },
    ]);
    expect(bars).toHaveLength(2);
    expect(bars[0]).toEqual({
      time: 1704080700,
      open: 100,
      high: 105,
      low: 99,
      close: 104,
      volume: 1000,
      oi: 50,
    });
    expect(bars[1].time).toBe(1704080760);
    expect(bars[1].oi).toBe(0); // zero is a real reading, not absent
  });

  it("omits oi when the source candle has none", () => {
    const bars = toOpenAlgoBars([{ ts: "2024-01-01T09:15:00", open: 1, high: 1, low: 1, close: 1, volume: 0, oi: null }]);
    expect(bars[0].oi).toBeUndefined();
  });

  it("preserves ordering (time is strictly increasing)", () => {
    const bars = toOpenAlgoBars([
      { ts: "2024-01-01T09:15:00", open: 1, high: 2, low: 1, close: 2, volume: 0, oi: null },
      { ts: "2024-01-01T09:16:00", open: 2, high: 2, low: 1, close: 1, volume: 0, oi: null },
      { ts: "2024-01-01T09:17:00", open: 1, high: 3, low: 1, close: 3, volume: 0, oi: null },
    ]);
    expect(bars.map((b) => b.time)).toEqual([1704080700, 1704080760, 1704080820]);
  });
});

describe("chart defaults", () => {
  it("defaults the per-panel toggle to point-and-figure", () => {
    expect(DEFAULT_CHART_TYPE).toBe("point-figure");
    expect(CHART_TYPES[0]).toBe("point-figure");
    expect(CHART_TYPES).toEqual(["point-figure", "renko", "candlestick"]);
  });

  it("ships the agreed box-size presets and defaults", () => {
    expect(BOX_PRESETS).toEqual([0.1, 0.25, 0.5, 1, 3]);
    expect(DEFAULT_BOX_PCT).toBe(0.1);
    expect(DEFAULT_COMBINED_BOX_PCT).toBe(0.5);
    expect(DEFAULT_REVERSAL).toBe(3);
  });
});
