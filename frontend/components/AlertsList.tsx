import { formatDateTime } from "@/lib/format";
import type { Alert } from "@/lib/types";

const DOT_COLOR: Record<Alert["alert_type"], string> = {
  screener_signal: "#5B9BD5",
  profit_target: "#3DD68C",
  max_loss: "#F85149",
  margin_call: "#F0B429",
};

export default function AlertsList({ alerts }: { alerts: Alert[] }) {
  return (
    <div className="rounded-[10px] border p-3" style={{ background: "#12161C", borderColor: "#1F2630" }}>
      <div className="mb-2 text-xs font-semibold text-[#C7CDD6]">RECENT ALERTS</div>
      <div className="flex flex-col gap-[7px]">
        {alerts.length === 0 && <div className="py-2 text-xs text-[#8B96A5]">No alerts yet.</div>}
        {alerts.slice(0, 6).map((a) => (
          <div key={a.id} className="flex items-start gap-2" style={{ opacity: a.is_read ? 0.6 : 1 }}>
            <span
              className="mt-[5px] h-1.5 w-1.5 flex-shrink-0 rounded-full"
              style={{ background: DOT_COLOR[a.alert_type] }}
            />
            <div className="text-[11.5px] leading-relaxed">
              {a.message} <span className="text-[#8B96A5]">· {formatDateTime(a.created_at)}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
