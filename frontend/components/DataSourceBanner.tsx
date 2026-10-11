import type { ScreenerStatus } from "@/lib/types";

/** Always-visible provenance banner. Signals are only trustworthy when the
 * screener status is "ok"; sample data and degraded runs are called out loudly. */
export default function DataSourceBanner({ status }: { status: ScreenerStatus | null }) {
  if (!status || status.state === "ok" || status.state === "starting") return null;
  const isSample = status.state === "sample";
  return (
    <div
      role="alert"
      className="px-4 py-2 text-center text-xs font-semibold"
      style={{
        background: isSample ? "#3A2A00" : "#3A0F12",
        color: isSample ? "#FFC857" : "#FF8A8A",
        borderBottom: "1px solid #232C36",
      }}
    >
      {isSample ? "SAMPLE DATA \u2014 not real market prices. Do not trade on these signals." : "NO LIVE DATA \u2014 signals are paused."}{" "}
      <span className="font-normal opacity-80">{status.message}</span>
    </div>
  );
}
