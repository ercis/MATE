import { Skeleton } from "@/components/ui/skeleton";
import { PageContainer } from "@/components/page";

// Mirrors the variant-detail page: rank/badge row + stat grid (VariantHeader),
// sequence strip, the histogram/attribute card pair, then the case-list table.
// The topbar owns the breadcrumb and title, so nothing here draws them. Used by
// both the route-level loading.tsx and the page's isLoading branch so the two
// skeletons are identical — no flash when the query takes over from the route
// transition.
export function VariantDetailSkeleton() {
  return (
    <PageContainer className="space-y-8">
      <header className="space-y-3">
        <Skeleton className="h-5 w-40" />
        <div className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3 lg:grid-cols-6">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="space-y-1">
              <Skeleton className="h-2.5 w-14" />
              <Skeleton className="h-4 w-20" />
            </div>
          ))}
        </div>
      </header>
      <Skeleton className="h-12 w-full rounded-lg" />
      <div className="grid gap-6 lg:grid-cols-2">
        {Array.from({ length: 2 }).map((_, i) => (
          <div key={i} className="space-y-3 rounded-lg border p-4">
            <Skeleton className="h-4 w-44" />
            <Skeleton className="h-64 w-full" />
          </div>
        ))}
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
