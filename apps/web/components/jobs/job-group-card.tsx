"use client";

import { ChevronDown, ChevronRight } from "lucide-react";

import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { StatusBadge } from "@/components/status-badge";
import { JobChildRow, PrecomputeStepRow } from "@/components/jobs/job-child-row";
import { useModuleNames } from "@/lib/queries";
import { parseJobTitle, type JobGroup } from "@/lib/stores/jobs";
import { cn } from "@/lib/cn";

interface JobGroupCardProps {
  group: JobGroup;
  expanded: boolean;
  onToggle: () => void;
}

/**
 * One import group as a single collapsible card. The header is the dropdown
 * trigger and carries an honest "N of M steps" determinate bar (derived from how
 * many child module jobs have finished). When expanded, the child step rows are
 * nested condensed *inside* the card instead of floating as flat rows below it.
 */
export function JobGroupCard({ group, expanded, onToggle }: JobGroupCardProps) {
  const { parent, children, steps, done, total } = group;
  // Resolved once per group, not per row - keeps the `GET /modules` fetch scoped
  // to "the drawer is open and there is a group", never firing from its shell.
  const moduleName = useModuleNames();
  const pct = total > 0 ? Math.min(100, Math.floor((done / total) * 100)) : 0;
  const hasRows = steps ? steps.length > 0 : children.length > 0;
  const { name: cleanTitle, badge } = parseJobTitle(parent);
  const status = group.active ? "running" : parent.status;

  return (
    <Card className="gap-0 overflow-hidden py-0">
      <button
        type="button"
        aria-expanded={expanded}
        aria-label={`${expanded ? "Collapse" : "Expand"} ${cleanTitle} steps`}
        onClick={onToggle}
        className={cn(
          "w-full cursor-pointer space-y-2 p-3 text-left transition-colors",
          "hover:bg-accent/40",
          "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring",
        )}
      >
        <div className="flex items-start justify-between gap-2">
          <div className="flex min-w-0 flex-1 items-center gap-1.5">
            {expanded ? (
              <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
            ) : (
              <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
            )}
            <Badge
              variant="outline"
              className="h-5 shrink-0 whitespace-nowrap border-0 bg-muted px-1.5 text-[10px]"
            >
              {badge}
            </Badge>
            <div className="truncate text-sm font-medium leading-tight">{cleanTitle}</div>
          </div>
          <StatusBadge status={status} />
        </div>

        <div className="space-y-0.5 pl-[1.375rem]">
          <Progress value={pct} className="h-1" />
          <div className="flex items-center justify-between text-[11px] text-muted-foreground tabular-nums">
            <span>
              {done} / {total} {total === 1 ? "step" : "steps"}
            </span>
            <span>{pct}%</span>
          </div>
        </div>
      </button>

      {expanded && hasRows && (
        <div className="border-t border-border bg-muted/20 px-1 py-1">
          {steps
            ? steps.map((step) =>
                step.job ? (
                  <JobChildRow key={step.job.id} job={step.job} />
                ) : (
                  <PrecomputeStepRow
                    key={step.moduleId}
                    moduleId={step.moduleId}
                    name={moduleName(step.moduleId)}
                    state={step.state as "waiting" | "skipped"}
                    waitingOnNames={step.waitingOn.map(moduleName)}
                  />
                ),
              )
            : children.map((child) => <JobChildRow key={child.id} job={child} />)}
        </div>
      )}
    </Card>
  );
}
