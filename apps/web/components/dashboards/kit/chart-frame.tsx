"use client";

import type { ReactNode } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/cn";

/**
 * Wrapper every charting card should render its plot inside.
 *
 * It exists for one non-obvious reason: **recharts' `ResponsiveContainer`
 * needs a parent with a *definite* height**, and inside a dashboard card the
 * flex chain can collapse to zero. `flex-1` plus a `min-height` is not enough —
 * `height: 100%` has nothing to resolve against, recharts measures -1×-1, and
 * the chart renders as nothing at all. Giving the plot its own
 * absolutely-positioned box inside a `relative` slot fixes it, because
 * `inset-0` always sizes to the slot's content box.
 *
 * Promoted from `modules/log_evolution`, where it was discovered — the trap is
 * not specific to that module, so neither is the fix.
 *
 * Also carries the loading/empty states, so a charting widget does not need a
 * separate `CardShell` around it.
 */
export function ChartFrame({
  loading,
  empty,
  error,
  emptyText = "No data for this log yet.",
  errorText = "Could not load this chart.",
  legend,
  caption,
  /** Floor for the plot area itself, below which a chart stops being readable
   * regardless of what the card's own minimum allows. */
  minPlotHeight = 140,
  className,
  children,
}: {
  loading?: boolean;
  empty?: boolean;
  error?: unknown;
  emptyText?: string;
  errorText?: string;
  /** Series legend. Always render one for two or more series — identity must
   * never be carried by colour alone. */
  legend?: ReactNode;
  caption?: ReactNode;
  minPlotHeight?: number;
  className?: string;
  children: ReactNode;
}) {
  if (loading) return <Skeleton className="h-full min-h-24 w-full" />;
  if (error) return <Message>{errorText}</Message>;
  if (empty) return <Message>{emptyText}</Message>;

  return (
    <div className={cn("flex h-full w-full flex-col", className)}>
      {legend ? (
        <div className="mb-1 flex shrink-0 flex-wrap items-center gap-x-4 gap-y-1.5 text-[10px] text-muted-foreground">
          {legend}
        </div>
      ) : null}
      <div className="relative min-h-0 w-full flex-1" style={{ minHeight: minPlotHeight }}>
        <div className="absolute inset-0">{children}</div>
      </div>
      {caption ? (
        <div className="mt-1 shrink-0 text-[10px] text-muted-foreground">{caption}</div>
      ) : null}
    </div>
  );
}

function Message({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-full min-h-24 items-center justify-center px-3 text-center text-xs text-muted-foreground">
      {children}
    </div>
  );
}

/** One legend entry: a colour chip and its label.
 *
 * Pass `color` from `seriesColor(i)` rather than a literal — a legend that
 * disagrees with the marks it describes is worse than none. */
export function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <span className="flex min-w-0 items-center gap-1.5">
      <span
        className="inline-block h-2.5 w-2.5 shrink-0 rounded-[2px]"
        style={{ backgroundColor: color }}
      />
      <span className="max-w-[16ch] truncate" title={label}>
        {label}
      </span>
    </span>
  );
}
