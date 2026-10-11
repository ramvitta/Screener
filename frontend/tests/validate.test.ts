import { describe, expect, it } from "vitest";
import { MAX_PRICE, formatRupees, legPnl, parsePrice } from "../lib/validate";

describe("parsePrice", () => {
  it("accepts plain non-negative numbers", () => {
    expect(parsePrice("42.5")).toBe(42.5);
    expect(parsePrice(" 0 ")).toBe(0); // expired worthless is valid
    expect(parsePrice(String(MAX_PRICE))).toBe(MAX_PRICE);
  });

  it.each(["", "  ", "abc", "NaN", "Infinity", "-Infinity", "-5", "1e9", "1,000", "0x10", "5.", ".5", String(MAX_PRICE + 1)])(
    "rejects %j",
    (input) => {
      expect(parsePrice(input)).toBeNull();
    },
  );

  it("treats a cancelled prompt as no input", () => {
    expect(parsePrice(null)).toBeNull();
  });
});

describe("legPnl", () => {
  it("matches the backend: short profits when price falls", () => {
    expect(legPnl({ action: "SELL", entry_price: 100, qty: 75 }, 40)).toBe(4500);
    expect(legPnl({ action: "BUY", entry_price: 60, qty: 75 }, 20)).toBe(-3000);
  });

  it("rounds to paise (no float noise)", () => {
    expect(legPnl({ action: "SELL", entry_price: 10.1, qty: 3 }, 9.8)).toBe(0.9);
  });
});

describe("formatRupees", () => {
  it("formats sign and grouping", () => {
    expect(formatRupees(-1500)).toBe("-₹1,500.00");
    expect(formatRupees(0)).toBe("₹0.00");
  });
});
