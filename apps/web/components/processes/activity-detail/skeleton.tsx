import { Skeleton } from "@/components/ui/skeleton";
import { PageContainer } from "@/components/page";

// Mirrors the activity-detail page: name row + stat grid (ActivityHeader),
// actions row, the containing-variants card, then the case-list table. The
// topbar owns the breadcrumb and title, so nothing here draws them. Used by
// both the route-level loading.tsx and the page's isLoading branch so the two
// skeletons are identical — no flash when the query takes over from the route
// transition.
export function ActivityDetailSkeleton() {
  return (
    <PageContainer className="space-y-8">
      <header className="space-y-3">
        <Skeleton className="h-6 w-56" />
        <div className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3 lg:grid-cols-5">
          {Array.from({ length: 10 }).map((_, i) => (
            <div key={i} className="space-y-1">
              <Skeleton className="h-2.5 w-14" />
              <Skeleton className="h-4 w-20" />
            </div>
          ))}
        </div>
      </header>
      <div className="flex gap-2">
        <Skeleton className="h-8 w-56 rounded-md" />
        <Skeleton className="h-8 w-40 rounded-md" />
      </div>
      <div className="rounded-lg border">
        <div className="border-b px-4 py-3">
          <Skeleton className="h-4 w-56" />
        </div>
        <div className="space-y-2 p-4">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      </div>
      <div className="rounded-lg border">
        <div className="border-b px-4 py-3">
          <Skeleton className="h-4 w-48" />
        </div>
        <div className="space-y-2 p-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      </div>
    </PageContainer>
  );
}
