import type { StrategyType } from "@/lib/types";

const STRATEGIES: { key: StrategyType | "all"; label: string }[] = [
  { key: "all", label: "All" },
  { key: "bull_put_spread", label: "Bull Put" },
  { key: "bear_call_spread", label: "Bear Call" },
  { key: "iron_condor", label: "Iron Condor" },
];

export default function FilterBar({
  strategy,
  onStrategyChange,
}: {
  strategy: StrategyType | "all";
  onStrategyChange: (s: StrategyType | "all") => void;
}) {
  return (
    <div
      style={{ borderBottom: "1px solid #1A2028", background: "#0E1116" }}
      className="flex flex-shrink-0 items-center gap-2.5 px-6 py-[9px]"
    >
      <span className="mr-1 text-xs text-[#8B96A5]">Strategy</span>
      <div className="flex gap-0.5 rounded-[7px] bg-[#171C23] p-[3px]">
        {STRATEGIES.map((s) => (
          <button
            key={s.key}
            onClick={() => onStrategyChange(s.key)}
            className="rounded-[5px] px-3 py-1.5 text-xs"
            style={{
              background: strategy === s.key ? "#232C36" : "transparent",
              color: strategy === s.key ? "#E8EAED" : "#8B96A5",
              fontWeight: strategy === s.key ? 600 : 400,
            }}
          >
            {s.label}
          </button>
        ))}
      </div>
      <div className="mx-1.5 h-5 w-px bg-[#1F2630]" />
      <span className="text-xs text-[#8B96A5]">Min POP</span>
      <div className="rounded-[7px] border border-[#232C36] px-2.5 py-1.5 text-xs">65%</div>
      <span className="text-xs text-[#8B96A5]">Min IV Rank</span>
      <div className="rounded-[7px] border border-[#232C36] px-2.5 py-1.5 text-xs">40%</div>
      <span className="text-xs text-[#8B96A5]">DTE</span>
      <div className="rounded-[7px] border border-[#232C36] px-2.5 py-1.5 text-xs">7–45</div>
      <div className="flex-grow" />
      <span className="text-xs text-[#8B96A5]">Sort by</span>
      <div className="flex items-center gap-1.5 rounded-[7px] border border-[#232C36] px-2.5 py-1.5 text-xs">
        Risk-adjusted return
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#8B96A5" strokeWidth="2">
          <path d="M6 9l6 6 6-6" />
        </svg>
      </div>
    </div>
  );
}
