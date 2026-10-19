import StrategySignalsPage from "@/components/StrategySignalsPage";

export default async function StrategyPage({
  params,
}: {
  params: Promise<{ key: string }>;
}) {
  const { key } = await params;
  return <StrategySignalsPage key={key} familyKey={key} />;
}
