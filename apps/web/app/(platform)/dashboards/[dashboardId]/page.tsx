import { ModuleRuntimeWarmup } from "@/components/module-runtime-warmup";
import { DashboardView } from "@/components/dashboards/dashboard-view";

export default async function DashboardDetailPage({
  params,
}: {
  params: Promise<{ dashboardId: string }>;
}) {
  const { dashboardId } = await params;
  // Warm the module runtime only on routes that render module widgets (this +
  // process detail), not globally - see components/module-runtime-warmup.
  return (
    <>
      <ModuleRuntimeWarmup />
      <DashboardView dashboardId={dashboardId} />
    </>
  );
}
