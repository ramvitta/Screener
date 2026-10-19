export function formatCurrency(value: number): string {
  const sign = value < 0 ? "-" : "";
  return `${sign}₹${Math.abs(value).toLocaleString("en-IN", {
    maximumFractionDigits: 0,
  })}`;
}

export function formatPct(value: number, digits = 0): string {
  return `${(value * 100).toFixed(digits)}%`;
}

export function formatStrategyLabel(strategy: string): string {
  const overrides: Record<string, string> = {
    adx_direction: "ADX Direction",
    unified: "Unified",
  };
  if (overrides[strategy]) return overrides[strategy];
  return strategy
    .split("_")
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(" ");
}

export function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-IN", {
    day: "2-digit",
    month: "short",
  });
}

export function formatDateTime(iso: string): string {
  return `${formatDate(iso)} ${formatTime(iso)}`;
}

export function daysToExpiry(expiryIso: string): number {
  const ms = new Date(expiryIso).getTime() - Date.now();
  return Math.max(0, Math.round(ms / 86_400_000));
}

export function isMonthlyExpiry(expiryIso: string): boolean {
  const d = new Date(expiryIso);
  const lastDayOfMonth = new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate();
  return lastDayOfMonth - d.getDate() < 7;
}

export function riskAdjustedReturn(riskReward: { max_profit: number; max_loss: number }): number {
  return riskReward.max_loss === 0 ? 0 : riskReward.max_profit / riskReward.max_loss;
}

export function formatRatio(value: number, digits = 2): string {
  return value.toFixed(digits);
}
