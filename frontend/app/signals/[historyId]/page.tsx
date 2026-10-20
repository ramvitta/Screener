import SignalDetailView from "@/components/SignalDetailView";
import Link from "next/link";

export default async function SignalDetailPage({
  params,
}: {
  params: Promise<{ historyId: string }>;
}) {
  const { historyId } = await params;
  const id = Number.parseInt(historyId, 10);
  if (Number.isNaN(id)) {
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-3 bg-[#0B0E11] text-sm text-[#8B96A5]">
        <span>Invalid signal id.</span>
        <Link href="/" className="text-[#5B9BD5]">
          ← Back to dashboard
        </Link>
      </div>
    );
  }
  return <SignalDetailView key={id} historyId={id} />;
}
