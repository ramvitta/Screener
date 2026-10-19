import SignalDetailView from "@/components/SignalDetailView";

export default async function SignalDetailPage({
  params,
}: {
  params: Promise<{ historyId: string }>;
}) {
  const { historyId } = await params;
  const id = Number.parseInt(historyId, 10);
  if (Number.isNaN(id)) {
    return (
      <div className="flex h-screen items-center justify-center bg-[#0B0E11] text-sm text-[#8B96A5]">
        Invalid signal id.
      </div>
    );
  }
  return <SignalDetailView key={id} historyId={id} />;
}
