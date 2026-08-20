import { DashboardView } from "@/components/dashboards/dashboard-view";

export default async function DashboardDetailPage({
  params,
}: {
  params: Promise<{ dashboardId: string }>;
}) {
  const { dashboardId } = await params;
  return <DashboardView dashboardId={dashboardId} />;
}
