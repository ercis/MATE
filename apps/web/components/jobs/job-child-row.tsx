"use client";

import { type MouseEvent } from "react";
import { Check, Circle, Clock, Loader2, Minus, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { useCancelJob } from "@/lib/queries";
import { parseJobTitle, type LiveJob } from "@/lib/stores/jobs";
import { jobProgress, stageLabel } from "@/lib/job-progress";
import { formatDuration } from "@/lib/format";
import { cn } from "@/lib/cn";

/**
 * A compact checklist row for one child (module) job inside an import group.
 * The leading icon tells the step's state at a glance; the live trailer keeps
 * an indeterminate child feeling alive ("{n} processed") instead of a dead
 * pulse, and shows a real bar once the child reports a fraction or total.
 */
export function JobChildRow({ job }: { job: LiveJob }) {
  const cancel = useCancelJob();
  const { name: cleanTitle } = parseJobTitle(job);
  const { pct, label, eta } = jobProgress(job);
  const running = job.status === "running";
  // Cheap ETA: only when the step reports a determinate fraction (jobProgress
  // derives `eta` from elapsed rate × remaining). Indeterminate steps skip it.
  const runningLabel =
    eta != null && Number.isFinite(eta) ? `${label} · ETA ${formatDuration(eta)}` : label;
  const isError = job.status === "failed";
  const isActive =
    job.status === "running" || job.status === "queued" || job.status === "paused";

  return (
    <div className="flex items-start gap-2 rounded-md px-2 py-1.5 pl-[1.875rem]">
      <StepIcon status={job.status} />
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex items-center justify-between gap-2">
          <div className="truncate text-xs font-medium leading-tight">{cleanTitle}</div>
          <div className="flex shrink-0 items-center gap-1">
            <span
              className={cn(
                "text-[11px] tabular-nums",
                isError ? "text-destructive" : "text-muted-foreground",
              )}
            >
              {isError ? "Failed" : running ? runningLabel : statusLabel(job.status)}
            </span>
            {isActive && (
              <Button
                size="sm"
                variant="ghost"
                aria-label="Cancel"
                className="h-5 w-5 cursor-pointer p-0"
                // Child rows sit inside the group card's expand/collapse button,
                // so swallow the click to avoid toggling the group.
                onClick={(e: MouseEvent) => {
                  e.stopPropagation();
                  cancel.mutate(job.id);
                }}
                disabled={cancel.isPending}
              >
                {cancel.isPending ? (
                  <Loader2 className="h-3 w-3 animate-spin" />
                ) : (
                  <X className="h-3 w-3" />
                )}
              </Button>
            )}
          </div>
        </div>
        {running && (
          <Progress
            value={pct ?? undefined}
            className={cn("h-1", pct === null && "animate-pulse")}
          />
        )}
        {(job.stage || job.message) && running && (
          <div className="truncate text-[11px] text-muted-foreground">
            {job.stage && <span className="font-medium">{stageLabel(job.stage)}</span>}
            {job.stage && job.message && <span className="mx-1">·</span>}
            {job.message}
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * A checklist row for a precompute step that has *no* job yet: either `waiting`
 * (its upstream hasn't finished, so the platform hasn't submitted it) or
 * `skipped` (an upstream failed, so its `<upstream>.completed` trigger will never
 * fire). Mirrors `JobChildRow`'s layout so the checklist stays aligned.
 *
 * `name`/`waitingOnNames` are display names resolved by the caller (one
 * `useModuleNames()` per group, not per row). They fall back to the raw id, so a
 * row is never blank; the id stays reachable as the row's `title`, and the
 * drawer's id-based filter keeps working.
 */
export function PrecomputeStepRow({
  moduleId,
  name,
  state,
  waitingOnNames,
}: {
  moduleId: string;
  name: string;
  state: "waiting" | "skipped";
  waitingOnNames: string[];
}) {
  const label =
    state === "skipped"
      ? "Skipped"
      : waitingOnNames.length > 0
        ? `Waiting on ${waitingOnNames.join(", ")}`
        : "Waiting";

  return (
    <div className="flex items-start gap-2 rounded-md px-2 py-1.5 pl-[1.875rem]">
      {state === "skipped" ? (
        <Minus className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground/40" />
      ) : (
        <Clock className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground/60" />
      )}
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2">
          <div
            title={moduleId}
            className={cn(
              "truncate text-xs font-medium leading-tight",
              state === "skipped"
                ? "text-muted-foreground line-through"
                : "text-muted-foreground",
            )}
          >
            {name}
          </div>
          <span className="shrink-0 text-[11px] tabular-nums text-muted-foreground">
            {label}
          </span>
        </div>
      </div>
    </div>
  );
}

function StepIcon({ status }: { status: string }) {
  if (status === "running") {
    return <Loader2 className="mt-0.5 h-3.5 w-3.5 shrink-0 animate-spin text-foreground" />;
  }
  if (status === "completed") {
    return <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-chart-2" />;
  }
  if (status === "failed") {
    return <X className="mt-0.5 h-3.5 w-3.5 shrink-0 text-destructive" />;
  }
  // queued / paused / cancelled
  return <Circle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground/50" />;
}

function statusLabel(status: string): string {
  if (status === "completed") return "Done";
  if (status === "queued") return "Queued";
  if (status === "paused") return "Paused";
  if (status === "cancelled") return "Cancelled";
  return "";
}
