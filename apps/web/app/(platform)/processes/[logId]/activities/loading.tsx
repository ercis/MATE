import { ActivityDetailSkeleton } from "@/components/processes/activity-detail/skeleton";

// Same frame as the page's isLoading branch, so the route-level skeleton and
// the query skeleton are identical — no flash between them.
export default function Loading() {
  return <ActivityDetailSkeleton />;
}
