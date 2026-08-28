"use client";

import { useState } from "react";
import Link from "next/link";
import { Check, ChevronDown, ChevronRight, Circle, Loader2, Minus, X } from "lucide-react";

import { CountUp } from "@/components/count-up";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import type { EventLogDetail } from "@/lib/api-types";
import { cn } from "@/lib/cn";
import type {
  ImportModuleStep,
  ImportStage,
  StageState,
} from "@/components/processes/import/use-import-stages";

/**
 * The live import, as a checklist. Deliberately mirrors the jobs drawer's
 * icon/state vocabulary (`job-child-row.tsx`) so the same import doesn't look
 * like two different things in two places.
 */
export function ImportProgress({
  stages,
  fileName,
  error,
  log,
  logId,
  onRetry,
}: {
  stages: ImportStage[];
  fileName: string;
  error?: string | null;
  /** Set once the log row is readable – powers the finished summary. */
  log?: EventLogDetail | null;
  logId: string | null;
  onRetry: () => void;
}) {
  const ready = log?.status === "ready";

  return (
    <div className="space-y-5">
      <header className="space-y-1">
        <h2 className="text-base font-semibold">
          {ready ? "Import complete" : error ? "Import failed" : "Importing"}
        </h2>
        <p className="text-sm text-muted-foreground">
          {ready
            ? "Your process is ready to explore."
            : error
              ? error
              : `${fileName} - you can leave this page, the import keeps running.`}
        </p>
      </header>

      <ol className="space-y-1">
        {stages.map((stage) => (
          <StageRow key={stage.key} stage={stage} />
        ))}
      </ol>

      {ready && log && <ReadySummary log={log} />}

      <div className="flex flex-wrap justify-end gap-2 border-t border-border pt-4">
        {error ? (
          <>
            <Button variant="outline" asChild className="cursor-pointer">
              <Link href="/processes">Back to processes</Link>
            </Button>
            <Button onClick={onRetry} className="cursor-pointer">
              Try again
            </Button>
          </>
        ) : (
          <>
            <Button variant="outline" asChild className="cursor-pointer">
              <Link href="/processes">All processes</Link>
            </Button>
            <Button asChild disabled={!logId} className="cursor-pointer">
              <Link href={logId ? `/processes/${logId}` : "/processes"}>
                {ready ? "Open process" : "Open anyway"}
              </Link>
            </Button>
          </>
        )}
      </div>
    </div>
  );
}

function StageRow({ stage }: { stage: ImportStage }) {
  const active = stage.state === "active";
  const modules = stage.modules ?? [];

  return (
    <li
      className={cn(
        "flex items-start gap-2.5 rounded-md px-2 py-2 transition-colors",
        active && "bg-accent/40",
      )}
    >
      <StageIcon state={stage.state} />
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex items-center justify-between gap-2">
          <span
            className={cn(
              "truncate text-sm",
              stage.state === "pending" && "text-muted-foreground",
              stage.state === "skipped" && "text-muted-foreground line-through",
              active && "font-medium",
            )}
          >
            {stage.label}
          </span>
          {stage.detail && (
            <span
              className={cn(
                "shrink-0 text-[11px] tabular-nums",
                stage.state === "failed" ? "text-destructive" : "text-muted-foreground",
              )}
            >
              {stage.detail}
            </span>
          )}
        </div>
        {active && (
          <Progress
            value={stage.pct ?? undefined}
            className={cn("h-1", (stage.pct === null || stage.pct === undefined) && "animate-pulse")}
          />
        )}
        {modules.length > 0 && (
          <ModuleBreakdown steps={modules} defaultOpen={stage.state !== "done"} />
        )}
      </div>
    </li>
  );
}

/**
 * The precompute closure, one row per module. Open while the modules are still
 * running (that's the phase where "which one is this waiting on?" is the whole
 * question) and folded away once they're all done - a settled list of 17 green
 * checks is noise. A manual toggle wins over both.
 */
function ModuleBreakdown({
  steps,
  defaultOpen,
}: {
  steps: ImportModuleStep[];
  defaultOpen: boolean;
}) {
  const [override, setOverride] = useState<boolean | null>(null);
  const open = override ?? defaultOpen;
  const running = steps.filter((s) => s.state === "active").length;

  return (
    <div className="pt-1">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOverride(!open)}
        className="flex cursor-pointer items-center gap-1 rounded text-[11px] text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
      >
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        <span>
          {open ? "Hide" : "Show"} modules
          {running > 0 && ` · ${running} running`}
        </span>
      </button>

      {open && (
        <ul className="mt-1 space-y-0.5 border-l border-border pl-2">
          {steps.map((step) => (
            <ModuleRow key={step.moduleId} step={step} />
          ))}
        </ul>
      )}
    </div>
  );
}

function ModuleRow({ step }: { step: ImportModuleStep }) {
  const active = step.state === "active";

  return (
    <li className="flex items-start gap-2 rounded-md px-1 py-1">
      <StageIcon state={step.state} className="mt-[3px] h-3 w-3" />
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex items-center justify-between gap-2">
          <span
            title={step.moduleId}
            className={cn(
              "truncate text-xs",
              step.state === "pending" && "text-muted-foreground",
              step.state === "skipped" && "text-muted-foreground line-through",
              active && "font-medium",
            )}
          >
            {step.name}
          </span>
          <span
            className={cn(
              "shrink-0 text-[11px] tabular-nums",
              step.state === "failed" ? "text-destructive" : "text-muted-foreground",
            )}
          >
            {step.detail}
          </span>
        </div>
        {active && (
          <Progress
            value={step.pct ?? undefined}
            className={cn("h-0.5", step.pct === null && "animate-pulse")}
          />
        )}
      </div>
    </li>
  );
}

function StageIcon({ state, className }: { state: StageState; className?: string }) {
  const base = cn("mt-0.5 h-4 w-4 shrink-0", className);
  if (state === "active") {
    return <Loader2 className={cn(base, "animate-spin text-foreground")} />;
  }
  if (state === "done") {
    return <Check className={cn(base, "animate-in zoom-in-50 text-chart-2 duration-200")} />;
  }
  if (state === "failed") {
    return <X className={cn(base, "text-destructive")} />;
  }
  if (state === "skipped") {
    return <Minus className={cn(base, "text-muted-foreground/40")} />;
  }
  return <Circle className={cn(base, "text-muted-foreground/40")} />;
}

function ReadySummary({ log }: { log: EventLogDetail }) {
  const stats: { label: string; value: number | null }[] =
    log.log_model === "object_centric"
      ? [
          { label: "Events", value: log.events_count },
          { label: "Objects", value: log.objects_count },
          { label: "Object types", value: log.object_types_count },
        ]
      : [
          { label: "Events", value: log.events_count },
          { label: "Cases", value: log.cases_count },
          { label: "Variants", value: log.variants_count },
        ];

  return (
    <div className="grid grid-cols-3 gap-3 rounded-lg border border-chart-2/30 bg-chart-2/5 p-4">
      {stats.map((stat) => (
        <div key={stat.label} className="space-y-0.5">
          <div className="text-xl font-semibold tabular-nums">
            <CountUp value={stat.value} />
          </div>
          <div className="text-xs text-muted-foreground">{stat.label}</div>
        </div>
      ))}
    </div>
  );
}
