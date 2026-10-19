import type { StrategyType } from "./types";

/**
 * Strategy *families* shown on the home page. This is a broader concept than
 * `StrategyType` (which is the backend's position/screener enum): a family
 * can aggregate several concrete strategies (e.g. "Income Spreads" = Bull
 * Put + Bear Call + Iron Condor), and the straddle/strangle/ADX/unified
 * families map to the str-stra screener's StrategyType values.
 */

export type StrategyFamilyKey =
  | "straddle"
  | "strangle"
  | "income_spreads"
  | "adx_direction"
  | "unified";

export type StrategyFamilyStatus = "live" | "planned";

export interface StrategyFamily {
  key: StrategyFamilyKey;
  label: string;
  tagline: string;
  description: string;
  accent: string;
  badgeBg: string;
  badgeFg: string;
  /** Concrete `StrategyType`s this family aggregates (empty => not yet emitted). */
  strategyTypes: StrategyType[];
  /** Value passed to `/screener?strategy=...` when the tile is opened. */
  target: StrategyType | "all";
  status: StrategyFamilyStatus;
}

export const STRATEGY_FAMILIES: StrategyFamily[] = [
  {
    key: "straddle",
    label: "Straddle",
    tagline: "Direction-neutral volatility play",
    description:
      "ATM CE + PE on the same strike. Screens for tight CE↔PE parity (≤2% spread) and low/neutral ADX to flag a range-bound market ahead of a breakout.",
    accent: "#8B96A5",
    badgeBg: "#1B2129",
    badgeFg: "#C7CDD6",
    strategyTypes: ["straddle"],
    target: "straddle",
    status: "live",
  },
  {
    key: "strangle",
    label: "Strangle",
    tagline: "Wider wings, lower premium",
    description:
      "OTM CE + PE across two strikes. Same parity/ADX logic as the straddle, but with wider strikes for cheaper entry and a larger profit band.",
    accent: "#8B96A5",
    badgeBg: "#1B2129",
    badgeFg: "#C7CDD6",
    strategyTypes: ["strangle"],
    target: "strangle",
    status: "live",
  },
  {
    key: "income_spreads",
    label: "Income Spreads",
    tagline: "Defined-risk credit collection",
    description:
      "Bull Put, Bear Call and Iron Condor on NIFTY. Screens IV Rank, POP, short delta and credit/max-loss to surface high-probability premium trades.",
    accent: "#3DD68C",
    badgeBg: "#173324",
    badgeFg: "#3DD68C",
    strategyTypes: ["bull_put_spread", "bear_call_spread", "iron_condor"],
    target: "all",
    status: "live",
  },
  {
    key: "adx_direction",
    label: "ADX Direction",
    tagline: "Trend + volume confirmation",
    description:
      "Uses a falling ADX (14, Wilder) into Neutral plus a supply candle on below-average volume — the 'inaction' setup — to time directional entries.",
    accent: "#5B9BD5",
    badgeBg: "#152538",
    badgeFg: "#5B9BD5",
    strategyTypes: ["adx_direction"],
    target: "adx_direction",
    status: "live",
  },
  {
    key: "unified",
    label: "Unified Screener",
    tagline: "Three passes, one ranked feed",
    description:
      "Combines ADX-neutral, supply-candle/inaction and straddle-parity checks into a single tiered output: ★ PRIME (3 passes), ★ READY (2), ★ WATCH (1).",
    accent: "#F0B429",
    badgeBg: "#241A12",
    badgeFg: "#F0B429",
    strategyTypes: ["unified"],
    target: "unified",
    status: "live",
  },
];

export const VALID_STRATEGY_TYPES: StrategyType[] = [
  "bull_put_spread",
  "bear_call_spread",
  "iron_condor",
  "straddle",
  "strangle",
  "adx_direction",
  "unified",
];

export function isStrategyType(value: string | null): value is StrategyType {
  return value !== null && (VALID_STRATEGY_TYPES as string[]).includes(value);
}
