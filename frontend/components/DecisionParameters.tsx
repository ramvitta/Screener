import type { ScreenerRuleRef } from "@/lib/types";

const TONE_COLOR: Record<ScreenerRuleRef["tone"], string> = {
  green: "#3DD68C",
  blue: "#5B9BD5",
  amber: "#F0B429",
};

// Mirrors the Phase 1 screener defaults documented in SRS_Architecture.md
// §4.2 / screener_rules.params_json — shown here as a static reference until
// the rules panel reads live values from GET /api/v1/screener/rules.
const RULES: ScreenerRuleRef[] = [
  { label: "IV Rank", value: "≥ 40%", tone: "green", note: "Richer premium for credit strategies" },
  { label: "POP", value: "≥ 65%", tone: "green", note: "Minimum acceptable win probability" },
  { label: "Short Δ", value: "0.15–0.30", tone: "blue", note: "Standard OTM credit-spread band" },
  { label: "DTE", value: "7–45 d", tone: "blue", note: "Avoid gamma risk (<7d) & slow theta (>45d)" },
  { label: "Credit/Max Loss", value: "≥ 0.33", tone: "amber", note: "~1:3 minimum risk-adjusted return" },
  { label: "RSI(14)", value: "40–60", tone: "amber", note: "Neutral/range → favors Iron Condor" },
];

export default function DecisionParameters() {
  return (
    <div className="rounded-[10px] border p-3.5" style={{ background: "#12161C", borderColor: "#1F2630" }}>
      <div className="mb-2.5 flex items-center justify-between">
        <div className="text-xs font-semibold text-[#C7CDD6]">DECISION PARAMETERS</div>
        <div className="text-[10px] text-[#8B96A5]">Baseline ranges · tunable per rule</div>
      </div>
      <div className="flex flex-col gap-1.5">
        {RULES.map((r) => (
          <div key={r.label} className="flex items-center gap-2 text-[11px]">
            <span className="w-24 text-[#8B96A5]">{r.label}</span>
            <span className="mono w-[70px] font-semibold">{r.value}</span>
            <span className="h-1.5 w-9 rounded-[3px]" style={{ background: TONE_COLOR[r.tone] }} />
            <span className="flex-grow text-[#8B96A5]">{r.note}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
