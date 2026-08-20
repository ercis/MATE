import { Skeleton } from "@/components/ui/skeleton";
import { CardGridSkeleton } from "@/components/skeletons";

// Mirrors DashboardView: a sticky toolbar bar + the canvas card grid, so the
// toolbar chrome appears instantly and the cards pulse where they'll land.
export default function Loading() {
  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border px-4 py-2.5 sm:px-6 lg:px-8">
        <Skeleton className="h-8 w-8 rounded-md" />
        <Skeleton className="h-6 w-48" />
        <Skeleton className="ml-auto h-8 w-24 rounded-md" />
        <Skeleton className="h-8 w-20 rounded-md" />
      </div>
      <div className="flex-1 overflow-auto px-4 py-6 sm:px-6 lg:px-8">
        <CardGridSkeleton count={6} />
      </div>
    </div>
  );
}
