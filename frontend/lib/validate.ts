/** Input validation + P&L preview for the manual exit flows. Mirrors the
 * backend bounds (app/models/schemas.py) so bad input is caught before a
 * request is made; the backend still re-validates everything. */

export const MAX_PRICE = 10_000_000;

/** Parses a user-typed price. Returns null for anything that isn't a finite
 * number in [0, MAX_PRICE] (empty, NaN, Infinity, negative, absurdly large). */
export function parsePrice(input: string | null): number | null {
  if (input === null) return null;
  const trimmed = input.trim();
  if (trimmed === "" || !/^\d+(\.\d+)?$/.test(trimmed)) return null;
  const value = Number(trimmed);
  return Number.isFinite(value) && value >= 0 && value <= MAX_PRICE ? value : null;
}

export interface PnlLeg {
  action: "BUY" | "SELL";
  entry_price: number;
  qty: number;
}

/** Realised P&L if `leg` is closed at `exitPrice`, rounded to paise. */
export function legPnl(leg: PnlLeg, exitPrice: number): number {
  const diff = leg.action === "SELL" ? leg.entry_price - exitPrice : exitPrice - leg.entry_price;
  return Math.round(diff * leg.qty * 100) / 100;
}

export function formatRupees(value: number): string {
  const sign = value < 0 ? "-" : "";
  return `${sign}\u20B9${Math.abs(value).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}
