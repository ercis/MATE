"use client";

/**
 * Tiny presentation kit shared by the log_evolution widgets. Inlined into each
 * widget bundle (apps/web/scripts/bundle-modules.mjs), so keep it dependency-light
 * (only runtime externals).
 *
 * `ChartFrame` is the important bit: it guarantees the recharts
 * `ResponsiveContainer` always has a parent with a *definite* non-zero height.
 * Inside a dashboard card / RGL grid cell the flex chain can collapse to 0, and
 * a bare `flex-1` + `min-height` does NOT give the container a definite height
 * for `height: 100%` to resolve against – recharts then measures -1×-1 and
 * renders nothing. We give the chart its own absolutely-positioned box
 * (`absolute inset-0`) inside a `relative` flex slot: inset-0 always sizes to
 * the slot's content box, so `height: 100%` resolves and recharts can measure.
 */
import type { ReactNode } from "react";

import { Skeleton } from "@/components/ui/skeleton";

export function ChartFrame({
  loading,
  empty,
  emptyText = "No data for this log yet.",
  legend,
  caption,
  children,
}: {
  loading?: boolean;
  empty?: boolean;
  emptyText?: string;
  legend?: ReactNode;
  caption?: ReactNode;
  children: ReactNode;
}) {
  if (loading) return <Skeleton className="h-full min-h-24 w-full" />;
  if (empty)
    return (
      <div className="flex h-full min-h-24 items-center justify-center text-center text-xs text-muted-foreground">
        {emptyText}
      </div>
    );
  return (
    <div className="flex h-full w-full flex-col">
      {legend ? (
        <div className="mb-1 flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-muted-foreground">
          {legend}
        </div>
      ) : null}
      <div className="relative min-h-0 w-full flex-1" style={{ minHeight: 140 }}>
        <div className="absolute inset-0">{children}</div>
      </div>
      {caption ? <div className="mt-1 shrink-0 text-[10px] text-muted-foreground">{caption}</div> : null}
    </div>
  );
}

export function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className="inline-block h-2.5 w-2.5 rounded-[2px]" style={{ backgroundColor: color }} />
      <span className="truncate">{label}</span>
    </span>
  );
}
