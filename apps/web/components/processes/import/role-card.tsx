"use client";

import { Sparkles } from "lucide-react";

import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { ColumnRoleQuality, ProbeColumn } from "@/lib/api-types";
import { cn } from "@/lib/cn";
import { stagger } from "@/lib/stagger";

export const NONE_VALUE = "__none__";

/** The canonical roles, in the order the mapping step presents them. */
export const REQUIRED_ROLES = ["case_id", "activity", "timestamp"] as const;
export const OPTIONAL_ROLES = ["end_timestamp", "resource", "cost"] as const;

export const ROLE_LABELS: Record<string, string> = {
  case_id: "Case ID",
  activity: "Activity",
  timestamp: "Timestamp",
  end_timestamp: "End timestamp",
  resource: "Resource",
  cost: "Cost",
};

export const ROLE_HINTS: Record<string, string> = {
  case_id: "Groups events into one run of the process",
  activity: "The step that was performed",
  timestamp: "When the step happened",
  end_timestamp: "When the step finished, if the log records both",
  resource: "Who or what performed the step",
  cost: "Cost attached to the step",
};

/** How the mapping was arrived at – shown so a guess never passes as a fact. */
const QUALITY_LABEL: Record<ColumnRoleQuality, string> = {
  user: "Your choice",
  exact: "Matched",
  fuzzy: "Guessed",
  fallback: "Inferred from data",
};

export function isLowConfidence(quality: ColumnRoleQuality | undefined): boolean {
  return quality === "fuzzy" || quality === "fallback" || quality === undefined;
}

/**
 * One canonical role: which column feeds it, how confident that is, and what
 * the data in that column actually looks like. The sample values are the whole
 * point of the step - they turn "trust the guess" into a two-second check.
 */
export function RoleCard({
  role,
  value,
  quality,
  columns,
  required,
  index,
  aiSuggested,
  onChange,
}: {
  role: string;
  value: string | undefined;
  quality: ColumnRoleQuality | undefined;
  columns: ProbeColumn[];
  required?: boolean;
  index: number;
  /** Filled by the AI second opinion rather than the resolver. */
  aiSuggested?: boolean;
  onChange: (column: string | undefined) => void;
}) {
  const column = columns.find((c) => c.name === value);
  const unset = !value;
  const low = required && isLowConfidence(quality);

  return (
    <div
      style={stagger(index)}
      className={cn(
        "animate-in fade-in-0 slide-in-from-bottom-1 fill-mode-both space-y-2 rounded-lg border p-3 duration-300",
        required && unset
          ? "border-destructive/40 bg-destructive/5"
          : low
            ? "border-amber-500/40 bg-amber-500/5"
            : "border-border bg-surface",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <Label className="text-sm font-medium">
            {ROLE_LABELS[role] ?? role}
            {required && <span className="ml-1 text-destructive">*</span>}
          </Label>
          <p className="mt-0.5 text-xs text-muted-foreground">{ROLE_HINTS[role]}</p>
        </div>
        {aiSuggested && !unset ? (
          <span className="inline-flex shrink-0 items-center gap-1 rounded-sm bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-primary">
            <Sparkles className="h-2.5 w-2.5" />
            AI
          </span>
        ) : (
          <QualityChip quality={quality} unset={unset} required={required} />
        )}
      </div>

      <Select
        value={value ?? NONE_VALUE}
        onValueChange={(v) => onChange(v === NONE_VALUE ? undefined : v)}
      >
        <SelectTrigger className="w-full cursor-pointer">
          <SelectValue placeholder="Pick a column" />
        </SelectTrigger>
        <SelectContent>
          {!required && (
            <SelectItem value={NONE_VALUE} className="cursor-pointer">
              – not in this log
            </SelectItem>
          )}
          {columns.map((c) => (
            <SelectItem key={c.name} value={c.name} className="cursor-pointer">
              {c.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <SampleValues column={column} />
    </div>
  );
}

function QualityChip({
  quality,
  unset,
  required,
}: {
  quality: ColumnRoleQuality | undefined;
  unset: boolean;
  required?: boolean;
}) {
  if (unset) {
    return (
      <span
        className={cn(
          "shrink-0 rounded-sm px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide",
          required
            ? "bg-destructive/10 text-destructive"
            : "bg-muted text-muted-foreground",
        )}
      >
        {required ? "Not mapped" : "Optional"}
      </span>
    );
  }
  if (!quality) return null;

  const guessed = quality === "fuzzy" || quality === "fallback";
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1 rounded-sm px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide",
        guessed
          ? "bg-amber-500/10 text-amber-700 dark:text-amber-400"
          : quality === "user"
            ? "bg-primary/10 text-primary"
            : "bg-chart-2/15 text-chart-2",
      )}
    >
      {guessed && <Sparkles className="h-2.5 w-2.5" />}
      {QUALITY_LABEL[quality]}
    </span>
  );
}

function SampleValues({ column }: { column: ProbeColumn | undefined }) {
  if (!column) {
    return (
      <p className="text-xs text-muted-foreground/70">
        Pick a column to preview its values.
      </p>
    );
  }
  if (column.samples.length === 0) {
    return <p className="text-xs text-amber-700 dark:text-amber-400">No values in the sample.</p>;
  }
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap gap-1">
        {column.samples.map((sample) => (
          <code
            key={sample}
            className="max-w-full truncate rounded bg-muted px-1.5 py-0.5 font-mono text-[11px]"
          >
            {sample}
          </code>
        ))}
      </div>
      {column.coverage < 1 && (
        <p className="text-[11px] text-amber-700 dark:text-amber-400">
          Empty in {Math.round((1 - column.coverage) * 100)}% of the sampled rows
        </p>
      )}
    </div>
  );
}
