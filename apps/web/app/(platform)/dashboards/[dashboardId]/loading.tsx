import { DashboardViewSkeleton } from "@/components/dashboards/dashboard-view-skeleton";

// Same frame as the dynamic boundary's fallback (DashboardViewSkeleton), so the
// route-level (RSC/data) skeleton and the chunk-load skeleton are identical -
// no flash between them.
export default function Loading() {
  return <DashboardViewSkeleton />;
}
