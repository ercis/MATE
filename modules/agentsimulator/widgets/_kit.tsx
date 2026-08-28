"use client";

/**
 * Presentation kit shared by the AgentSimulator widgets and panel. Each widget
 * is bundled independently, so keep this small and limited to runtime externals
 * (ui/skeleton, ui/button, ui/tooltip, lucide, next/navigation) plus sibling
 * module files (`./metric-info` for the fidelity ⓘ popovers). `COLORS` is the
 * real-vs-simulated palette used everywhere.
 *
 * `CardShell` is more than chrome: when a widget has no result it renders a
 * self-serve empty state ("Run simulation" starts the job right from the
 * dashboard) and, while a simulate job for the log is queued/running –
 * whoever started it – a live progress state instead. Compact by design: a
 * default-size dashboard card gives the body roughly 110px of height, so no
 * big icon circle, no py-16 (the platform `EmptyState` is panel-sized).
 */
import type { ReactNode } from "react";
import { Bot, Loader2, Play } from "lucide-react";
import { useRouter } from "next/navigation";

import { seriesColor } from "@/components/dashboards/kit";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

import { useSimulationGate, type MetricCell, type SimJob } from "../panel/queries";
import { MetricInfoHint } from "./metric-info";

/**
 * Real vs. simulated, from the platform's shared series palette.
 *
 * These were hardcoded hex (`#6366f1` / `#f59e0b`), so this module's cards
 * were the only ones on a board not following the theme — and the pair had
 * never been checked for colourblind separation. Slots 1 and 2 are validated
 * adjacent and follow light/dark.
 */
export const COLORS = { real: seriesColor(0), sim: seriesColor(1) } as const;

/**
 * NOT the shared `CardShell` — this one gates on simulation state, showing a
 * run prompt or live progress when there are no results yet. It stays local
 * because that behaviour is specific to this module, not because the shared
 * kit was unsuitable for the rest.
 */
export function CardShell({
  logId,
  loading,
  empty,
  refreshing,
  children,
}: {
  logId: string;
  loading?: boolean;
  empty?: boolean;
  /** Results query is refetching (e.g. right after a run finished) – bridge
   * the gap with a skeleton instead of flashing the run prompt. */
  refreshing?: boolean;
  children: ReactNode;
}) {
  // Only watch the jobs list while the card would otherwise be empty – a card
  // that is showing charts doesn't poll at all.
  const gate = useSimulationGate(logId, { watch: Boolean(empty) });
  if (loading) return <Skeleton className="h-full min-h-24 w-full" />;
  if (empty && (gate.activeJob || gate.starting)) {
    return <SimulationRunning job={gate.activeJob} />;
  }
  if (empty && (gate.checking || refreshing)) {
    return <Skeleton className="h-full min-h-24 w-full" />;
  }
  if (empty) return <SimulationPrompt logId={logId} gate={gate} />;
  return <div className="h-full">{children}</div>;
}

/** Live progress while a simulate job for this log is queued/running. */
function SimulationRunning({ job }: { job: SimJob | null }) {
  const total = job?.progress_total ?? 0;
  const fraction =
    total > 0 ? Math.min(Math.max((job?.progress_current ?? 0) / total, 0), 1) : null;
  const stage =
    job?.message ||
    job?.stage ||
    (job?.status === "queued"
      ? "Queued…"
      : job?.status === "paused"
        ? "Paused"
        : "This can take several minutes.");
  return (
    <div className="flex h-full min-h-24 flex-col items-center justify-center gap-2 px-4 text-center">
      <div className="flex items-center gap-1.5 text-xs font-medium">
        <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
        Simulation running…
      </div>
      <div className="h-1.5 w-full max-w-56 overflow-hidden rounded-full bg-muted">
        {fraction == null ? (
          <div className="h-full w-full animate-pulse rounded-full bg-primary/40" />
        ) : (
          <div
            className="h-full rounded-full bg-primary transition-all"
            style={{ width: `${Math.round(fraction * 100)}%` }}
          />
        )}
      </div>
      <p className="line-clamp-2 max-w-64 text-[11px] text-muted-foreground">{stage}</p>
    </div>
  );
}

/** No result yet: explain what a simulation is and start one in place. */
function SimulationPrompt({
  logId,
  gate,
}: {
  logId: string;
  gate: ReturnType<typeof useSimulationGate>;
}) {
  const router = useRouter();
  const failed = gate.job?.status === "failed";
  const cancelled = gate.job?.status === "cancelled";
  return (
    <div className="flex h-full min-h-24 flex-col items-center justify-center gap-2 px-4 py-2 text-center">
      <div className="flex items-center gap-1.5 text-xs font-semibold">
        <Bot className="h-4 w-4 text-primary" />
        No simulation yet for this log
      </div>
      <p className="max-w-72 text-[11px] leading-snug text-muted-foreground">
        AgentSimulator learns agent behaviour from your log and generates synthetic runs to
        compare against the real process.
      </p>
      {failed && (
        <p className="max-w-72 text-[11px] text-destructive">Last simulation failed – try again.</p>
      )}
      {cancelled && (
        <p className="max-w-72 text-[11px] text-muted-foreground">
          Last simulation was cancelled.
        </p>
      )}
      {gate.startError != null && (
        <p className="max-w-72 text-[11px] text-destructive">
          Could not start the simulation – open the module page to check.
        </p>
      )}
      <div className="flex flex-wrap items-center justify-center gap-1.5">
        <Button size="sm" onClick={gate.start} disabled={gate.starting}>
          {gate.starting ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Play className="h-3.5 w-3.5" />
          )}
          Run simulation
        </Button>
        <Button
          size="sm"
          variant="ghost"
          className="text-muted-foreground"
          onClick={() =>
            router.push(`/processes/${encodeURIComponent(logId)}/modules/agentsimulator`)
          }
        >
          Configure
        </Button>
      </div>
    </div>
  );
}

/** One fidelity measure: short code + mean, with ± std, the full name, and an
 * ⓘ popover explaining the measure and citing the paper (`./metric-info`). */
export function MetricTile({ code, cell }: { code: string; cell?: MetricCell }) {
  const mean = cell?.mean;
  const value = mean == null ? "–" : mean < 10 ? mean.toFixed(3) : mean.toFixed(2);
  return (
    <div className="rounded-md border border-border/60 bg-muted/20 px-3 py-2">
      <div className="flex items-center justify-between gap-2">
        <span className="inline-flex items-center gap-1">
          <span className="text-[11px] font-semibold uppercase tracking-wide" title={cell?.label}>
            {code}
          </span>
          <MetricInfoHint metricKey={code} />
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
