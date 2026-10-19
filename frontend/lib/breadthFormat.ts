import type { BreadthMetrics } from "./types";

export type Bucket = "hour" | "day" | "week" | "month";

/** Plain-language verdict from a breadth snapshot. */
export function breadthVerdict(m: BreadthMetrics | null): { label: string; color: string; note: string } {
  if (!m) return { label: "No data yet", color: "#8B96A5", note: "Breadth is computed by the backend worker." };
  const ratio = m.breadth_ratio;
  const above20 = m.pct_above?.["20"];
  if (ratio == null) return { label: "Neutral", color: "#F0B429", note: "No decliners to compute a ratio." };
  if (ratio >= 1.2 && (above20 == null || above20 >= 50))
    return { label: "Constructive", color: "#3DD68C", note: "Advancers clearly lead and broad participation is holding." };
  if (ratio <= 0.8 || (above20 != null && above20 <= 30))
    return { label: "Deteriorating", color: "#F85149", note: "Decliners are leading or participation is narrow." };
  return { label: "Neutral", color: "#F0B429", note: "Advance/decline is balanced." };
}

export function breadthLabelFor(ts: string, bucket: Bucket): string {
  if (bucket === "hour") return ts.slice(11, 16);
  if (bucket === "month") return ts.slice(0, 7);
  return ts.slice(5); // MM-DD (day) or week-ending date
}
