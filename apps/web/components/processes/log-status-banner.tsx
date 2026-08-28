"use client";

import { useMemo } from "react";
import { AlertTriangle, ListChecks, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { precomputeProgressForLog, useJobsStore } from "@/lib/stores/jobs";
import type { EventLogDetail } from "@/lib/api-types";

/**
 * The lifecycle strip above a process's tabs.
 *
 * `processing` used to render nothing at all: the tabs were disabled, their
 * counts were blank, and the page gave no reason why. That is the state a log
 * sits in the longest right after an import, so it gets the fullest explanation
 * - what is running, how far along it is, and where to watch the steps.
 */
export function LogStatusBanner({ log }: { log: EventLogDetail }) {
  if (log.status === "importing") {
    return (
      <Strip>
        <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />
        <span>
          Reading your file — parsing events and building the case index. Modules and analytics
          unlock once this finishes.
        </span>
      </Strip>
    );
  }

  if (log.status === "processing") return <ProcessingStrip logId={log.id} />;

  if (log.status === "failed") {
    return (
      <div className="mb-4 flex items-start gap-2 rounded-lg border border-destructive/20 bg-destructive/10 px-3 py-2 text-sm text-destructive">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
        <span>Import failed: {log.error ?? "Unknown error"}</span>
      </div>
    );
  }

  return null;
}

function ProcessingStrip({ logId }: { logId: string }) {
  const setDrawerOpen = useJobsStore((s) => s.setDrawerOpen);
  // `precomputeProgressForLog` allocates a fresh object, so subscribe to the
  // stable map and derive inside a memo (see the note on `selectJobGroups`).
  const byId = useJobsStore((s) => s.byId);
  const progress = useMemo(() => precomputeProgressForLog(byId, logId), [byId, logId]);

  return (
    <div className="mb-4 space-y-2 rounded-lg border border-border bg-card px-3 py-2.5 text-sm">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span className="flex items-center gap-2 font-medium text-foreground">
          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />
          Preparing modules
        </span>
        <span className="min-w-0 flex-1 text-muted-foreground">
          Your log is parsed. Mate is precomputing every analysis module against it, in dependency
          order, so each one opens instantly afterwards.
        </span>
        <Button
          size="sm"
          variant="outline"
          className="h-7 shrink-0 cursor-pointer gap-1.5"
          onClick={() => setDrawerOpen(true)}
        >
          <ListChecks className="h-3.5 w-3.5" />
          View steps
        </Button>
      </div>
      {progress ? (
        <div className="space-y-1">
          <Progress value={progress.pct} className="h-1" />
          <p className="text-xs tabular-nums text-muted-foreground">
            {progress.done} of {progress.total} modules done
          </p>
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">
          This usually takes under a minute; large logs take longer.
        </p>
      )}
    </div>
  );
}

function Strip({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-4 flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-2 text-sm text-muted-foreground">
      {children}
    </div>
  );
}
