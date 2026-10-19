export function sentimentTone(bias: string | null | undefined): { color: string; bg: string; label: string } {
  switch (bias) {
    case "bullish":
    case "risk-on":
      return { color: "#3DD68C", bg: "#173324", label: (bias ?? "").toUpperCase() };
    case "bearish":
    case "risk-off":
      return { color: "#F85149", bg: "#2A171A", label: (bias ?? "").toUpperCase() };
    case "neutral":
      return { color: "#F0B429", bg: "#262018", label: "NEUTRAL" };
    default:
      return { color: "#8B96A5", bg: "#1B2129", label: "—" };
  }
}

export function directionTone(dir: string | null | undefined): string {
  if (dir === "long") return "#3DD68C";
  if (dir === "short") return "#F85149";
  return "#8B96A5";
}

export function shiftDate(date: string, delta: number): string {
  const [y, m, d] = date.split("-").map(Number);
  const t = new Date(Date.UTC(y, m - 1, d + delta));
  return t.toISOString().slice(0, 10);
}
