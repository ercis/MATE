"use client";

import { useEffect, useState, type KeyboardEvent, type MouseEvent } from "react";
import { Copy, Loader2, RefreshCcw, X } from "lucide-react";
import { toast } from "sonner";

import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { StatusBadge } from "@/components/status-badge";
import { useCancelJob, useRetryJob } from "@/lib/queries";
import { formatDuration, formatRelative } from "@/lib/format";
import { jobProgress, stageLabel } from "@/lib/job-progress";
import { jobStallSeconds, parseJobTitle, type LiveJob } from "@/lib/stores/jobs";
import { cn } from "@/lib/cn";

interface JobRowProps {
  job: LiveJob;
}

/**
 * A re-render tick so the stall hint advances without a `job.progress` event.
 * `null` disables the interval (a non-running job can never stall).
 */
function useNow(intervalMs: number | null): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (intervalMs === null) return;
    const t = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs]);
  return now;
}

export function JobRow({ job }: JobRowProps) {
  const cancel = useCancelJob();
  const retry = useRetryJob();
  const [detailsOpen, setDetailsOpen] = useState(false);

  const { pct, label, rate, eta } = jobProgress(job);

  const now = useNow(job.status === "running" ? 30_000 : null);
  const stalledSec = jobStallSeconds(job, now);

  const isActive =
    job.status === "running" || job.status === "queued" || job.status === "paused";
  const isFailed = job.status === "failed";

  const subtitle = job.subtitle ?? (job.module_id ? job.module_id : "");
  const { name: cleanTitle, badge: typeCategory } = parseJobTitle(job);

  // Action buttons live on the card; clicks on them must not also open the
  // details dialog (the rest of the card surface is the dialog trigger).
  const stop = (e: MouseEvent) => e.stopPropagation();

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      setDetailsOpen(true);
    }
  };

  return (
    <>
      <Card
        role="button"
        tabIndex={0}
        aria-label={`Open job details: ${cleanTitle}`}
        onClick={() => setDetailsOpen(true)}
        onKeyDown={onKeyDown}
        className={cn(
          "cursor-pointer space-y-2 p-3 transition-colors",
          "hover:border-primary/40 hover:bg-accent/40",
          "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
        )}
      >
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 min-w-0">
              <Badge
                variant="outline"
                className="h-5 shrink-0 whitespace-nowrap border-0 bg-muted px-1.5 text-[10px]"
              >
                {typeCategory}
              </Badge>
              <div className="truncate text-sm font-medium leading-tight">{cleanTitle}</div>
            </div>
            {subtitle && (
              <div className="truncate text-xs text-muted-foreground">{subtitle}</div>
            )}
          </div>
          <div className="flex items-center gap-1 shrink-0">
            <StatusBadge status={job.status} />
            {isActive && (
              <Button
                size="sm"
                variant="ghost"
                aria-label="Cancel"
                className="h-6 w-6 cursor-pointer p-0"
                onClick={(e) => {
                  stop(e);
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
            {isFailed && (
              <Button
                size="sm"
                variant="ghost"
                aria-label="Retry"
                className="h-6 w-6 cursor-pointer p-0"
                onClick={(e) => {
                  stop(e);
                  retry.mutate(job.id);
                }}
                disabled={retry.isPending}
              >
                {retry.isPending ? (
                  <Loader2 className="h-3 w-3 animate-spin" />
                ) : (
                  <RefreshCcw className="h-3 w-3" />
                )}
              </Button>
            )}
          </div>
        </div>

        {isActive && (
          <div className="space-y-0.5">
            <Progress
              value={pct ?? undefined}
              className={pct === null ? "h-1 animate-pulse" : "h-1"}
            />
            <div className="flex items-center justify-between text-[11px] text-muted-foreground tabular-nums">
              <span>{job.stage ? `${stageLabel(job.stage)} · ${label}` : label}</span>
              <span>
                {rate && Number.isFinite(rate)
                  ? `${Math.round(rate).toLocaleString()}/s · ETA ${formatDuration(eta)}`
                  : ""}
              </span>
            </div>
            {stalledSec !== null && (
              <div className="text-[11px] text-amber-600 dark:text-amber-500">
                No progress for {formatDuration(stalledSec)}
              </div>
            )}
          </div>
        )}
      </Card>

      <JobDetailsDialog job={job} open={detailsOpen} onOpenChange={setDetailsOpen} />
    </>
  );
}

function JobDetailsDialog({
  job,
  open,
  onOpenChange,
}: {
  job: LiveJob;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const subtitle = job.subtitle ?? (job.module_id ? job.module_id : "");
  const { name: cleanTitle, badge: typeCategory } = parseJobTitle(job);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-hidden sm:max-w-2xl">
        <DialogHeader>
          <div className="flex items-start justify-between gap-3 pr-6">
            <div className="min-w-0 flex-1 space-y-1">
              <div className="flex items-center gap-2 min-w-0">
                <Badge variant="outline" className="h-5 shrink-0 whitespace-nowrap border-0 bg-muted px-1.5 text-[10px]">
                  {typeCategory}
                </Badge>
                <DialogTitle className="truncate">{cleanTitle}</DialogTitle>
              </div>
              <DialogDescription className="truncate">{subtitle}</DialogDescription>
            </div>
            <StatusBadge status={job.status} />
          </div>
        </DialogHeader>

        <div className="-mr-2 max-h-[60vh] space-y-4 overflow-y-auto pr-2">
          <DetailGrid job={job} />

          {(job.stage || job.message) && (
            <div className="space-y-1">
              <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
                Status
              </div>
              <p className="text-xs">
                {job.stage && <span className="font-medium">{stageLabel(job.stage)}</span>}
                {job.stage && job.message && <span className="mx-1">·</span>}
                {job.message}
              </p>
            </div>
          )}

          <div className="space-y-1">
            <div className="flex items-center justify-between">
              <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
                Payload
              </div>
              <Button
                size="sm"
                variant="ghost"
                className="h-6 cursor-pointer gap-1 text-[11px]"
                onClick={() => {
                  navigator.clipboard.writeText(JSON.stringify(job.payload_json, null, 2));
                  toast.success("Payload copied");
                }}
              >
                <Copy className="h-3 w-3" />
                Copy
              </Button>
            </div>
            <pre className="max-h-64 overflow-y-auto overflow-x-hidden rounded-md bg-muted p-3 text-[11px]">
              {JSON.stringify(job.payload_json, null, 2)}
            </pre>
          </div>

          {job.error && (
            <div className="space-y-1">
              <div className="text-[10px] uppercase tracking-wide text-destructive">Error</div>
              <pre className="whitespace-pre-wrap break-words rounded-md bg-destructive/10 p-3 text-[11px] text-destructive">
                {job.error}
              </pre>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function DetailGrid({ job }: { job: LiveJob }) {
  const { pct, label, rate, eta } = jobProgress(job);

  const items: Array<{ label: string; value: React.ReactNode }> = [
    {
      label: "Job id",
      value: (
        <button
          type="button"
          onClick={() => {
            navigator.clipboard.writeText(job.id);
            toast.success("Job id copied");
          }}
          className="cursor-pointer truncate font-mono text-xs hover:underline"
          title={job.id}
        >
          {job.id}
        </button>
      ),
    },
    { label: "Type", value: <span className="truncate font-mono text-xs">{job.type}</span> },
    {
      label: "Module",
      value: (
        <span className="truncate font-mono text-xs">{job.module_id ?? "–"}</span>
      ),
    },
    { label: "Priority", value: <span className="tabular-nums">{job.priority}</span> },
    {
      label: "Progress",
      value: (
        <span className="tabular-nums">
          {pct === null && !job.progress_current ? "–" : label}
        </span>
      ),
    },
    {
      label: "Rate · ETA",
      value: (
        <span className="tabular-nums">
          {rate && Number.isFinite(rate)
            ? `${Math.round(rate).toLocaleString()}/s · ETA ${formatDuration(eta)}`
            : "–"}
        </span>
      ),
    },
    {
      label: "Created",
      value: <span>{formatRelative(job.created_at)}</span>,
    },
    {
      label: "Started",
      value: <span>{job.started_at ? formatRelative(job.started_at) : "–"}</span>,
    },
    {
      label: "Finished",
      value: <span>{job.finished_at ? formatRelative(job.finished_at) : "–"}</span>,
    },
    {
      label: "Parent",
      value: (
        <span className="truncate font-mono text-xs">{job.parent_job_id ?? "–"}</span>
      ),
    },
  ];

  return (
    <div className="grid grid-cols-2 gap-x-4 gap-y-2 rounded-md border border-border bg-muted/30 p-3 text-xs">
      {items.map((it) => (
        <div key={it.label} className="min-w-0 space-y-0.5">
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
            {it.label}
          </div>
          <div className="min-w-0 truncate">{it.value}</div>
        </div>
      ))}
    </div>
  );
}
