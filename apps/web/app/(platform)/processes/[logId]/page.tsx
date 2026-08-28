import { ModuleRuntimeWarmup } from "@/components/module-runtime-warmup";

import { ProcessDetailClient } from "./process-detail-client";

export default async function ProcessDetailPage({
  params,
}: {
  params: Promise<{ logId: string }>;
}) {
  const { logId } = await params;
  // Warm the module runtime only on routes that actually render module panels
  // (this + dashboards), not globally - see components/module-runtime-warmup.
  return (
    <>
      <ModuleRuntimeWarmup />
      <ProcessDetailClient logId={logId} />
    </>
  );
}
