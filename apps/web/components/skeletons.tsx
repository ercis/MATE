import * as React from "react";

import { cn } from "@/lib/cn";
import { Skeleton } from "@/components/ui/skeleton";
import { PageContainer } from "@/components/page";

// Composed loading shells built on the shadcn <Skeleton> primitive. Render the
// page chrome immediately on navigation/fetch so the user gets an instant,
// pulsing preview of where the data will land instead of a blank screen.
// Used by route-level loading.tsx files and in-page <Suspense>/isLoading paths.
//
// A skeleton only earns its keep if it reserves the space the real page will
// occupy — a placeholder for chrome the page never renders is worse than no
// placeholder at all, because the content visibly jumps when it resolves. The
// topbar owns the title, description and breadcrumb for every route now
// (lib/page-meta.ts), so nothing here draws them; a page header is only its
// action row.

/** A bordered card-table shell – mirrors the rounded-xl tables across the app. */
export function TableSkeleton({
  rows = 6,
  className,
}: {
  rows?: number;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "overflow-hidden rounded-xl border border-white/10 bg-card/60 backdrop-blur-sm",
        className,
      )}
    >
      <div className="flex items-center gap-4 border-b border-border px-4 py-3">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="ml-auto h-4 w-16" />
        <Skeleton className="h-4 w-16" />
      </div>
      <div className="divide-y divide-border">
        {Array.from({ length: rows }).map((_, i) => (
          <div key={i} className="flex items-center gap-4 px-4 py-3.5">
            <Skeleton className="h-8 w-8 shrink-0 rounded-md" />
            <div className="space-y-1.5">
              <Skeleton className="h-3.5 w-48" />
              <Skeleton className="h-3 w-28" />
            </div>
            <Skeleton className="ml-auto h-4 w-16" />
            <Skeleton className="h-4 w-12" />
          </div>
        ))}
      </div>
    </div>
  );
}

/** A responsive grid of card placeholders – modules, dashboards, etc. */
export function CardGridSkeleton({
  count = 6,
  className,
}: {
  count?: number;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "grid gap-4 sm:grid-cols-2 lg:grid-cols-3",
        className,
      )}
    >
      {Array.from({ length: count }).map((_, i) => (
        <div
          key={i}
          className="space-y-3 rounded-xl border border-white/10 bg-card/60 backdrop-blur-sm p-5"
        >
          <div className="flex items-center gap-3">
            <Skeleton className="h-9 w-9 rounded-lg" />
            <Skeleton className="h-4 w-28" />
          </div>
          <Skeleton className="h-3 w-full" />
          <Skeleton className="h-3 w-4/5" />
          <Skeleton className="h-8 w-24 rounded-md" />
        </div>
      ))}
    </div>
  );
}

/** A row of KPI stat-card placeholders – admin overview, detail headers. */
export function StatCardsSkeleton({
  count = 4,
  className,
}: {
  count?: number;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "grid gap-4 sm:grid-cols-2 lg:grid-cols-4",
        className,
      )}
    >
      {Array.from({ length: count }).map((_, i) => (
        <div
          key={i}
          className="space-y-3 rounded-xl border border-white/10 bg-card/60 backdrop-blur-sm p-5"
        >
          <Skeleton className="h-3 w-24" />
          <Skeleton className="h-7 w-20" />
        </div>
      ))}
    </div>
  );
}

/** A chart card placeholder – header line + a tall plot area. */
export function ChartCardSkeleton({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "space-y-4 rounded-xl border border-white/10 bg-card/60 backdrop-blur-sm p-5",
        className,
      )}
    >
      <div className="space-y-1.5">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="h-3 w-28" />
      </div>
      <Skeleton className="h-56 w-full rounded-lg" />
    </div>
  );
}

/**
 * Page header placeholder — the action row, and on the pages that have one a
 * left-aligned filter strip. Mirrors `<PageHeader>`, which is actions-only
 * across the app (`justify-end`, or `items-center` alongside filters).
 */
export function PageHeaderSkeleton({
  actions = 2,
  withFilters = false,
}: {
  /** Number of action buttons to reserve space for. */
  actions?: number;
  withFilters?: boolean;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-4 pb-6">
      {withFilters && <Skeleton className="h-9 w-60 rounded-md" />}
      <div className="ml-auto flex items-center gap-2">
        {Array.from({ length: actions }).map((_, i) => (
          <Skeleton key={i} className="h-9 w-32 rounded-md" />
        ))}
      </div>
    </div>
  );
}

/** Full page shell: action header + content (defaults to a table). */
export function PageSkeleton({
  children,
  actions,
  withFilters,
}: {
  children?: React.ReactNode;
  actions?: number;
  withFilters?: boolean;
}) {
  return (
    <PageContainer>
      <PageHeaderSkeleton actions={actions} withFilters={withFilters} />
      {children ?? <TableSkeleton />}
    </PageContainer>
  );
}

/**
 * Process detail shell — mirrors process-detail-client.tsx: a full-bleed tab
 * bar reading as an extension of the topbar, then the module card grid.
 *
 * Deliberately NOT wrapped in a single PageContainer: the bar spans the full
 * viewport and re-applies the container's cap + padding on an inner div, so the
 * triggers line up with the capped content below. Matching that here is what
 * keeps the tab row from shifting sideways when the real page takes over.
 */
export function DetailSkeleton() {
  return (
    <>
      <div className="border-b border-border">
        <div className="mx-auto flex w-full max-w-[1760px] items-center gap-3 px-4 sm:px-6 lg:px-8">
          {Array.from({ length: 5 }).map((_, i) => (
            // px-3 py-3 matches ProcessTabs' triggers, so the bar is the same
            // height before and after hydration.
            <div key={i} className="px-3 py-3">
              <Skeleton className="h-5 w-20" />
            </div>
          ))}
          <Skeleton className="ml-auto h-8 w-56 shrink-0 rounded-md" />
        </div>
      </div>
      <PageContainer>
        <CardGridSkeleton />
      </PageContainer>
    </>
  );
}

/** A module panel shell: the panel owns its own chrome, so just reserve the
 *  canvas the route will fill. */
export function PanelSkeleton() {
  return (
    <PageContainer>
      <Skeleton className="h-[70vh] w-full rounded-xl" />
    </PageContainer>
  );
}
