import { Skeleton } from "@/components/ui/skeleton";
import { CardGridSkeleton } from "@/components/skeletons";

// Light fallback for the dynamically-loaded DashboardView. Kept in its own file
// (importing only the light Skeleton/CardGridSkeleton) so the `loading:` prop of
// the next/dynamic boundary never pulls in dashboard-view-impl — and its heavy
// libs (framer-motion, react-grid-layout, recharts, @xyflow/react) — which would
// silently defeat the code-split. Mirrors the impl's toolbar + card-grid
// bounding box so the swap to the real view is layout-shift free.
export function DashboardViewSkeleton() {
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
