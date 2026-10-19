import { Suspense } from "react";
import LiveDashboard from "@/components/LiveDashboard";

// LiveDashboard reads `useSearchParams()` to pre-select a strategy filter, so
// it must sit under a Suspense boundary (Next prerenders this route).
export default function ScreenerPage() {
  return (
    <Suspense
      fallback={
        <div className="flex h-screen items-center justify-center bg-[#0B0E11] text-sm text-[#8B96A5]">
          Loading screener…
        </div>
      }
    >
      <LiveDashboard />
    </Suspense>
  );
}
