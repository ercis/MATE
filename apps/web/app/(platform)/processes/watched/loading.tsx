import { PageSkeleton } from "@/components/skeletons";
import { Skeleton } from "@/components/ui/skeleton";

// One action (New watched folder) + the stacked WatchCard rows — mirrors the
// page's isLoading branch so the route and query skeletons are identical.
export default function WatchedFoldersLoading() {
  return (
    <PageSkeleton actions={1}>
      <div className="space-y-3">
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-24 w-full" />
        ))}
      </div>
    </PageSkeleton>
  );
}
