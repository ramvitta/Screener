import ContractDetailView from "@/components/ContractDetailView";

export default async function ContractPage({
  params,
}: {
  params: Promise<{ signalId: string }>;
}) {
  const { signalId } = await params;
  return <ContractDetailView signalId={signalId} />;
}
