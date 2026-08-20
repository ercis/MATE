"use client";

import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { toastError } from "@/lib/toast";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { EventLogDetail, RemapColumnRoles } from "@/lib/api-types";
import { useRemapEventLog } from "@/lib/queries";

import { SectionShell } from "./general-section";

const REQUIRED: { key: keyof RemapColumnRoles; label: string; hint: string }[] = [
  { key: "case_id", label: "Case ID", hint: "Groups events into one process instance." },
  { key: "activity", label: "Activity", hint: "What happened at each event." },
  { key: "timestamp", label: "Timestamp", hint: "When the event occurred." },
];

const OPTIONAL: { key: keyof RemapColumnRoles; label: string }[] = [
  { key: "end_timestamp", label: "End timestamp" },
  { key: "resource", label: "Resource" },
  { key: "cost", label: "Cost" },
  { key: "role", label: "Role" },
  { key: "lifecycle", label: "Lifecycle" },
];

const NONE = "__none__";

interface DetectedSchema {
  source_columns?: unknown;
  columns?: unknown;
}

/**
 * Lets the user point the three mandatory roles (and optional ones) at the
 * right source columns. Saving re-imports the log from its retained original
 * with the chosen mapping – see `POST /event-logs/{id}/remap`.
 */
export function ColumnRolesSection({ logId, log }: { logId: string; log: EventLogDetail }) {
  const remap = useRemapEventLog(logId);

  const columns = useMemo(() => {
    const d = (log.detected_schema as DetectedSchema | null) ?? null;
    const src = Array.isArray(d?.source_columns) ? d.source_columns : d?.columns;
    return Array.isArray(src) ? (src as string[]) : [];
  }, [log.detected_schema]);

  const current = (log.column_roles ?? {}) as Record<string, string>;
  const [roles, setRoles] = useState<Record<string, string>>(current);

  useEffect(() => {
    setRoles((log.column_roles ?? {}) as Record<string, string>);
  }, [log.id, log.column_roles]);

  const set = (key: string, value: string) =>
    setRoles((prev) => {
      const next = { ...prev };
      if (value === NONE) delete next[key];
      else next[key] = value;
      return next;
    });

  const requiredComplete = REQUIRED.every((r) => roles[r.key]);
  const dirty = JSON.stringify(roles) !== JSON.stringify(current);

  const onSave = async () => {
    if (!requiredComplete) return;
    try {
      await remap.mutateAsync(roles as unknown as RemapColumnRoles);
      toast.success("Re-importing with the new column mapping");
    } catch (err) {
      toastError(`Re-map failed: ${(err as Error).message}`);
    }
  };

  if (columns.length === 0) {
    return (
      <SectionShell title="Column roles" description="Map your columns to the canonical roles.">
        <p className="text-xs text-muted-foreground">
          No column information is available for this log yet.
        </p>
      </SectionShell>
    );
  }

  const picker = (key: string, placeholder = "Not set") => (
    <Select value={roles[key] ?? NONE} onValueChange={(v) => set(key, v)}>
      <SelectTrigger className="h-8 w-full text-sm">
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={NONE}>{placeholder}</SelectItem>
        {columns.map((c) => (
          <SelectItem key={c} value={c}>
            {c}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );

  return (
    <SectionShell
      title="Column roles"
      description="Tell Mate which source column holds each canonical field. Saving re-imports the log."
    >
      {log.mapping_needs_review && (
        <div className="mb-3 flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-300">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>
            The importer guessed one or more mandatory columns. Confirm the mapping below,
            then save to re-import.
          </span>
        </div>
      )}

      <div className="max-w-2xl space-y-4">
        <div className="space-y-3">
          {REQUIRED.map((r) => (
            <div key={r.key} className="grid grid-cols-[140px_1fr] items-center gap-3">
              <div>
                <Label className="text-xs font-medium">
                  {r.label} <span className="text-amber-500">*</span>
                </Label>
                <p className="text-[11px] leading-tight text-muted-foreground">{r.hint}</p>
              </div>
              {picker(r.key)}
            </div>
          ))}
        </div>

        <details className="group">
          <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">
            Optional roles
          </summary>
          <div className="mt-3 space-y-3">
            {OPTIONAL.map((r) => (
              <div key={r.key} className="grid grid-cols-[140px_1fr] items-center gap-3">
                <Label className="text-xs font-medium text-muted-foreground">{r.label}</Label>
                {picker(r.key)}
              </div>
            ))}
          </div>
        </details>

        <div className="flex items-center gap-3">
          <Button
            type="button"
            size="sm"
            onClick={onSave}
            disabled={!requiredComplete || !dirty || remap.isPending}
          >
            {remap.isPending && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
            Save &amp; re-import
          </Button>
          {!requiredComplete && (
            <span className="text-xs text-muted-foreground">
              Pick a column for all three mandatory roles.
            </span>
          )}
        </div>
      </div>
    </SectionShell>
  );
}
