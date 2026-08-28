"use client";

import { TriangleAlert } from "lucide-react";

import type { LabelReport } from "./types";

/**
 * Surfaces activity-label mismatches between the reference model and the log -
 * the #1 reason a conformance run looks alarmingly red. Model tasks with no
 * matching log activity will deviate on every trace; log activities absent from
 * the model can never be replayed. Renders nothing when both sides line up.
 */
export function LabelReportBanner({ report }: { report: LabelReport | undefined }) {
  if (!report) return null;
  const missingInLog = report.in_model_not_log ?? [];
  const missingInModel = report.in_log_not_model ?? [];
  if (missingInLog.length === 0 && missingInModel.length === 0) return null;

  return (
    <div className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs">
      <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-500" />
      <div className="space-y-1.5">
        <p className="font-medium text-foreground">Activity names don&apos;t fully line up</p>
        {missingInLog.length > 0 && (
          <p className="text-muted-foreground">
            {missingInLog.length} reference{" "}
            {missingInLog.length === 1 ? "activity has" : "activities have"} no match in the log and
            will always show as deviations:{" "}
            <ChipList items={missingInLog} />
          </p>
        )}
        {missingInModel.length > 0 && (
          <p className="text-muted-foreground">
            {missingInModel.length} log{" "}
            {missingInModel.length === 1 ? "activity is" : "activities are"} not in the reference
            model: <ChipList items={missingInModel} />
          </p>
        )}
        <p className="text-muted-foreground">
          Names are compared exactly (ignoring case and extra spaces) — even a one-letter
          difference counts as a different activity. Rename the tasks in the model or the log
          activities to make them line up.
        </p>
      </div>
    </div>
  );
}

function ChipList({ items, max = 12 }: { items: string[]; max?: number }) {
  const shown = items.slice(0, max);
  const rest = items.length - shown.length;
  return (
    <span className="inline-flex flex-wrap gap-1 align-middle">
      {shown.map((s) => (
        <code
          key={s}
          className="rounded bg-background/70 px-1 py-0.5 font-mono text-[11px] text-foreground"
        >
          {s}
        </code>
      ))}
      {rest > 0 && <span className="text-muted-foreground">+{rest} more</span>}
    </span>
  );
}
