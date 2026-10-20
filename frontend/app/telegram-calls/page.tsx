import { Suspense } from "react";
import TelegramCallsView from "@/components/TelegramCallsView";

export default function TelegramCallsPage() {
  return (
    <Suspense fallback={null}>
      <TelegramCallsView />
    </Suspense>
  );
}
