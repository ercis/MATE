"use client";

/**
 * Presentation kit shared by the AgentSimulator widgets and panel. Each widget
 * is bundled independently, so keep this small and limited to runtime externals
 * (ui/skeleton only). `COLORS` is the real-vs-simulated palette used everywhere.
 */
import type { ReactNode } from "react";

import { Skeleton } from "@/components/ui/skeleton";

import type { MetricCell } from "../panel/queries";

export const COLORS = { real: "#6366f1", sim: "#f59e0b" } as const;

export function CardShell({
  loading,
  empty,
  emptyText = "No simulation yet – run one from the AgentSimulator panel.",
  children,
}: {
  loading?: boolean;
  empty?: boolean;
  emptyText?: string;
  children: ReactNode;
}) {
  if (loading) return <Skeleton className="h-full min-h-24 w-full" />;
  if (empty)
    return (
      <div className="flex h-full min-h-24 items-center justify-center px-4 text-center text-xs text-muted-foreground">
        {emptyText}
      </div>
    );
  return <div className="h-full">{children}</div>;
}

/** One fidelity measure: short code + mean, with ± std and the full name. */
export function MetricTile({ code, cell }: { code: string; cell?: MetricCell }) {
  const mean = cell?.mean;
  const value = mean == null ? "–" : mean < 10 ? mean.toFixed(3) : mean.toFixed(2);
  return (
    <div className="rounded-md border border-border/60 bg-muted/20 px-3 py-2">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-wide" title={cell?.label}>
          {code}
        </span>
        <span className="text-[9px] uppercase tracking-wide text-muted-foreground">↓ better</span>
      </div>
      <div className="mt-0.5 text-lg font-semibold tabular-nums tracking-tight">{value}</div>
      <div className="truncate text-[11px] text-muted-foreground">
        {cell?.std != null ? `± ${cell.std}` : ""}
        {cell?.label ? `${cell?.std != null ? " · " : ""}${cell.label}` : ""}
      </div>
    </div>
  );
}

export function LegendDots() {
  return (
    <div className="flex items-center gap-3 text-[11px] text-muted-foreground">
      <span className="inline-flex items-center gap-1">
        <span className="h-2 w-2 rounded-full" style={{ background: COLORS.real }} />
        Real (test)
      </span>
      <span className="inline-flex items-center gap-1">
        <span className="h-2 w-2 rounded-full" style={{ background: COLORS.sim }} />
        Simulated
      </span>
    </div>
  );
}
