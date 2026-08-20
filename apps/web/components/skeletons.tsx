import * as React from "react";

import { cn } from "@/lib/cn";
import { Skeleton } from "@/components/ui/skeleton";
import { PageContainer } from "@/components/page";

// Composed loading shells built on the shadcn <Skeleton> primitive. Render the
// page chrome immediately on navigation/fetch so the user gets an instant,
// pulsing preview of where the data will land instead of a blank screen.
// Used by route-level loading.tsx files and in-page <Suspense>/isLoading paths.

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
        "overflow-hidden rounded-xl border border-border bg-card",
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
          className="space-y-3 rounded-xl border border-border bg-card p-5"
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
          className="space-y-3 rounded-xl border border-border bg-card p-5"
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
        "space-y-4 rounded-xl border border-border bg-card p-5",
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

/** Page header placeholder (title + description + optional actions). */
export function PageHeaderSkeleton({ withActions = true }: { withActions?: boolean }) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-4 pb-6">
      <div className="space-y-2">
        <Skeleton className="h-7 w-48" />
        <Skeleton className="h-4 w-72" />
      </div>
      {withActions && (
        <div className="flex items-center gap-2">
          <Skeleton className="h-9 w-28 rounded-md" />
          <Skeleton className="h-9 w-32 rounded-md" />
        </div>
      )}
    </div>
  );
}

/** Full page shell: header + content (defaults to a table). */
export function PageSkeleton({
  children,
  withActions = true,
}: {
  children?: React.ReactNode;
  withActions?: boolean;
}) {
  return (
    <PageContainer>
      <PageHeaderSkeleton withActions={withActions} />
      {children ?? <TableSkeleton />}
    </PageContainer>
  );
}

/** Detail shell: breadcrumb + title + tab strip + KPI cards + canvas. */
export function DetailSkeleton() {
  return (
    <PageContainer>
      <div className="space-y-2 pb-6">
        <Skeleton className="h-3.5 w-40" />
        <Skeleton className="h-7 w-64" />
      </div>
      <div className="flex flex-wrap gap-2 border-b border-border pb-2">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-8 w-24 rounded-md" />
        ))}
      </div>
      <div className="grid gap-4 pt-6 sm:grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-24 w-full rounded-xl" />
        ))}
      </div>
      <Skeleton className="mt-6 h-80 w-full rounded-xl" />
    </PageContainer>
  );
}
