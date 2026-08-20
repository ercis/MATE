"use client";

import { useEffect, useMemo, useState } from "react";
import { RotateCcw, Search } from "lucide-react";
import { toast } from "sonner";

import { toastError } from "@/lib/toast";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import type { EventLogDetail } from "@/lib/api-types";
import { useActivities, useUpdateEventLog } from "@/lib/queries";
import { formatNumber } from "@/lib/format";
import { getActivityRenameMap } from "@/lib/activity-rename";
import { cn } from "@/lib/cn";

export interface ActivitiesTabProps {
  logId: string;
  log: EventLogDetail;
}

export function ActivitiesTab({ logId, log }: ActivitiesTabProps) {
  const { data, isLoading, isError, error } = useActivities(logId);
  const update = useUpdateEventLog(logId);

  const persistedMap = useMemo(() => getActivityRenameMap(log), [log]);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [filter, setFilter] = useState("");

  // Initialise / reset drafts whenever the persisted overrides change (e.g.
  // after a successful save or when switching logs).
  useEffect(() => {
    setDrafts({ ...persistedMap });
  }, [persistedMap]);

  const dirty = useMemo(() => {
    const draftKeys = Object.keys(drafts);
    const persistedKeys = Object.keys(persistedMap);
    if (draftKeys.length !== persistedKeys.length) return true;
    for (const k of draftKeys) {
      if ((drafts[k] ?? "") !== (persistedMap[k] ?? "")) return true;
    }
    return false;
  }, [drafts, persistedMap]);

  const filteredRows = useMemo(() => {
    if (!data) return [];
    const q = filter.trim().toLowerCase();
    if (!q) return data.rows;
    return data.rows.filter(
      (r) =>
        r.activity.toLowerCase().includes(q) ||
        (drafts[r.activity] ?? "").toLowerCase().includes(q),
    );
  }, [data, drafts, filter]);

  const setDraft = (raw: string, next: string) => {
    setDrafts((prev) => {
      const out = { ...prev };
      if (next.trim().length === 0) delete out[raw];
      else out[raw] = next;
      return out;
    });
  };

  const onSave = async () => {
    // Merge into the rest of column_overrides so we don't clobber labels/order.
    const nextOverrides = {
      ...(log.column_overrides ?? {}),
      activity_labels: Object.fromEntries(
        Object.entries(drafts).filter(([, v]) => v.trim().length > 0),
      ),
    };
    try {
      await update.mutateAsync({ column_overrides: nextOverrides });
      toast.success("Activity renames saved");
    } catch (err) {
      toastError(`Save failed: ${(err as Error).message}`);
    }
  };

  const onReset = () => {
    setDrafts({ ...persistedMap });
  };

  if (isError) {
    return (
      <div className="rounded-lg border border-destructive/20 bg-destructive/10 p-4 text-sm text-destructive">
        Could not load activities: {(error as Error)?.message ?? "Unknown error"}
      </div>
    );
  }

  const totalRenames = Object.keys(drafts).filter((k) => (drafts[k] ?? "").trim().length > 0).length;

  return (
    <div className="space-y-4">
      <div className="rounded-md border bg-muted/30 px-3 py-2 text-sm text-muted-foreground">
        Renames are display-only - the underlying event log keeps the raw activity names so
        analytics modules continue to operate on the canonical values. Leave a field blank to
        use the raw name.
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[220px] max-w-md">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Filter activities…"
            className="pl-8 h-9"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        </div>
        <span className="text-sm text-muted-foreground">
          {totalRenames > 0 ? (
            <>
              <span className="tabular-nums">{totalRenames}</span> renamed
            </>
          ) : (
            "No renames yet"
          )}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={onReset}
            disabled={!dirty || update.isPending}
            className="cursor-pointer"
          >
            <RotateCcw className="mr-1.5 h-3.5 w-3.5" />
            Discard changes
          </Button>
          <Button
            size="sm"
            onClick={onSave}
            disabled={!dirty || update.isPending}
            className="cursor-pointer"
          >
            {update.isPending ? "Saving…" : "Save renames"}
          </Button>
        </div>
      </div>

      <div className="rounded-lg border">
        {isLoading || !data ? (
          <div className="p-6 space-y-2">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-7 w-full" />
            ))}
          </div>
        ) : data.rows.length === 0 ? (
          <p className="px-4 py-8 text-center text-sm text-muted-foreground">
            This log has no events.
          </p>
        ) : (
          <Table>
            <TableHeader className="bg-muted/30">
              <TableRow>
                <TableHead className="w-[40%]">Activity</TableHead>
                <TableHead className="text-right">Events</TableHead>
                <TableHead>Display name</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filteredRows.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={3} className="h-24 text-center text-sm text-muted-foreground">
                    No activities match the filter.
                  </TableCell>
                </TableRow>
              ) : (
                filteredRows.map((row) => {
                  const draft = drafts[row.activity] ?? "";
                  const renamed = draft.trim().length > 0;
                  return (
                    <TableRow key={row.activity} className="h-12">
                      <TableCell>
                        <span
                          className={cn(
                            "font-mono text-xs",
                            renamed && "text-muted-foreground line-through",
                          )}
                        >
                          {row.activity}
                        </span>
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatNumber(row.count)}
                      </TableCell>
                      <TableCell>
                        <Input
                          className="h-8 text-sm"
                          value={draft}
                          onChange={(e) => setDraft(row.activity, e.target.value)}
                          placeholder={row.activity}
                        />
                      </TableCell>
                    </TableRow>
                  );
                })
              )}
            </TableBody>
          </Table>
        )}
      </div>
    </div>
  );
}
