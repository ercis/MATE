"use client";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import type { EventEditEntry } from "@/lib/api-types";
import { formatRelative } from "@/lib/format";
import { useEventEdits } from "@/lib/queries";

import { SectionShell } from "./general-section";

export function EditHistorySection({ logId }: { logId: string }) {
  const { data, isLoading } = useEventEdits(logId, 0, 25);

  return (
    <SectionShell
      title="Edit history"
      description="Manual cell edits applied to this log via the Events tab."
    >
      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-7 w-full" />
          ))}
        </div>
      ) : !data || data.rows.length === 0 ? (
        <p className="text-sm italic text-muted-foreground">
          No manual edits yet - use the Events tab&apos;s edit mode to fix missing values.
        </p>
      ) : (
        <div className="overflow-hidden rounded-md border">
          <Table>
            <TableHeader className="bg-muted/30">
              <TableRow>
                <TableHead>When</TableHead>
                <TableHead>Row</TableHead>
                <TableHead>Field</TableHead>
                <TableHead>Old → New</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.rows.map((row) => (
                <TableRow key={row.id} className="h-10">
                  <TableCell className="text-sm text-muted-foreground" title={row.edited_at}>
                    {formatRelative(row.edited_at)}
                  </TableCell>
                  <TableCell className="tabular-nums text-sm">#{row.row_index + 1}</TableCell>
                  <TableCell className="font-mono text-xs">{row.field}</TableCell>
                  <TableCell className="text-sm">
                    <ValueDiff old={row.old_value_json} next={row.new_value_json} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {data.total > data.rows.length && (
            <p className="border-t px-4 py-2 text-xs text-muted-foreground">
              Showing the latest {data.rows.length} of {data.total} edits.
            </p>
          )}
        </div>
      )}
    </SectionShell>
  );
}

function ValueDiff({ old: oldVal, next }: { old: unknown; next: unknown }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <ValueChip value={oldVal} muted />
      <span className="text-xs text-muted-foreground">→</span>
      <ValueChip value={next} />
    </span>
  );
}

function ValueChip({ value, muted = false }: { value: unknown; muted?: boolean }) {
  if (value === null || value === undefined) {
    return <span className="italic text-muted-foreground">empty</span>;
  }
  return (
    <span
      className={
        muted
          ? "rounded bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground line-through"
          : "rounded bg-muted px-1.5 py-0.5 font-mono text-[11px]"
      }
    >
      {String(value)}
    </span>
  );
}

// Reference EventEditEntry to avoid an unused-import lint flag while keeping types imported.
export type { EventEditEntry };
