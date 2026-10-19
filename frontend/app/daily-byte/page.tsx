import { Suspense } from "react";
import DailyByteView from "@/components/DailyByteView";

export default function DailyBytePage() {
  return (
    <Suspense fallback={null}>
      <DailyByteView />
    </Suspense>
  );
}
