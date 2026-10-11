import { SignalCardCompact, SignalCardExpanded } from "./SignalCard";
import type { ScreenerSignal } from "@/lib/types";

const EXPANDED_COUNT = 2;

export default function SignalFeed({
  signals,
  updatedAt,
}: {
  signals: ScreenerSignal[];
  updatedAt: string | null;
}) {
  const symbols = [...new Set(signals.map((s) => s.symbol))];

  return (
    <div className="flex w-[820px] flex-shrink-0 flex-col gap-3 overflow-y-auto pr-1">
      <div className="flex items-center justify-between">
        <div className="text-[13px] font-semibold text-[#C7CDD6]">
          SCREENER SIGNALS{" "}
          <span className="font-normal text-[#8B96A5]">
            · {signals.length} match{signals.length === 1 ? "" : "es"} across {symbols.join(" + ") || "—"}
          </span>
        </div>
        <div className="text-[11px] text-[#8B96A5]">{updatedAt ? `Updated ${updatedAt}` : ""}</div>
      </div>

      {signals.length === 0 && (
        <div
          className="rounded-[10px] border px-4 py-8 text-center text-sm text-[#8B96A5]"
          style={{ background: "#12161C", borderColor: "#1F2630" }}
        >
          No signals match the current filters.
        </div>
      )}

      {signals.map((s, i) =>
        i < EXPANDED_COUNT ? (
          <SignalCardExpanded key={s.signal_id} signal={s} />
        ) : (
          <SignalCardCompact key={s.signal_id} signal={s} />
        ),
      )}
    </div>
  );
}
