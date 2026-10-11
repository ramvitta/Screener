import { daysToExpiry, isMonthlyExpiry } from "@/lib/format";

export interface ExpiryChip {
  symbol: string;
  expiry: string;
}

/** Groups (symbol, expiry) pairs found in the current signal set and renders
 * them as selectable chips. Only symbols actually present in the data show
 * up — Phase 1 scope is NIFTY-only, so a second index appears automatically
 * once the backend screens it too. */
export default function ExpiryStrip({
  chips,
  selected,
  onSelect,
}: {
  chips: ExpiryChip[];
  selected: ExpiryChip | null;
  onSelect: (chip: ExpiryChip | null) => void;
}) {
  const bySymbol = new Map<string, ExpiryChip[]>();
  for (const c of chips) {
    const list = bySymbol.get(c.symbol) ?? [];
    list.push(c);
    bySymbol.set(c.symbol, list);
  }
  for (const list of bySymbol.values()) {
    list.sort((a, b) => a.expiry.localeCompare(b.expiry));
  }

  const symbols = [...bySymbol.keys()];

  return (
    <div
      style={{ borderBottom: "1px solid #1A2028", background: "#0E1116" }}
      className="flex flex-shrink-0 items-center gap-[18px] px-6 py-2.5"
    >
      {symbols.map((symbol, idx) => (
        <div key={symbol} className="flex items-center gap-[18px]">
          {idx > 0 && <div className="h-8 w-px bg-[#1F2630]" />}
          <div className="flex items-center gap-2.5">
            <span className="text-[11px] font-bold tracking-[0.4px] text-[#C7CDD6]">{symbol}</span>
            <div className="flex gap-1.5">
              {bySymbol.get(symbol)!.slice(0, 4).map((c) => {
                const isSelected = selected?.symbol === c.symbol && selected?.expiry === c.expiry;
                return (
                  <button
                    key={c.expiry}
                    onClick={() => onSelect(isSelected ? null : c)}
                    className="flex flex-col items-center gap-0.5 rounded-[7px] border px-2.5 py-[5px]"
                    style={{
                      background: isSelected ? "#232C36" : "#171C23",
                      borderColor: isSelected ? "#2E3844" : "#1F2630",
                    }}
                  >
                    <span className={`text-[11px] ${isSelected ? "font-semibold" : "text-[#C7CDD6]"}`}>
                      {new Date(c.expiry).toLocaleDateString("en-IN", { day: "2-digit", month: "short" })}
                    </span>
                    <span
                      className="mono text-[9px]"
                      style={{ color: isSelected ? "#3DD68C" : "#8B96A5" }}
                    >
                      DTE {daysToExpiry(c.expiry)} · {isMonthlyExpiry(c.expiry) ? "Monthly" : "Weekly"}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      ))}

      <div className="flex-grow" />
      <span className="text-[10.5px] text-[#8B96A5]">
        {selected
          ? `Filtered to ${selected.symbol} · ${new Date(selected.expiry).toLocaleDateString("en-IN", { day: "2-digit", month: "short" })} — click again to clear`
          : "Select a chip to filter the signal feed below"}
      </span>
    </div>
  );
}
