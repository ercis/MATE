import { ProcessDetailClient } from "./process-detail-client";

export default async function ProcessDetailPage({
  params,
}: {
  params: Promise<{ logId: string }>;
}) {
  const { logId } = await params;
  return <ProcessDetailClient logId={logId} />;
}
