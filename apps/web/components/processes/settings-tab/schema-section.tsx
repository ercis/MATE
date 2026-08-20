"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";

import { toastError } from "@/lib/toast";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { EventLogColumnOverrides, EventLogDetail } from "@/lib/api-types";
import { useUpdateEventLog } from "@/lib/queries";
import { formatDateRange, formatRelative } from "@/lib/format";

import { SectionShell } from "./general-section";

interface DetectedSchema {
  columns?: unknown;
  row_count?: unknown;
}

export function SchemaSection({ logId, log }: { logId: string; log: EventLogDetail }) {
  const update = useUpdateEventLog(logId);
  const detected = (log.detected_schema as DetectedSchema | null) ?? null;
  const columns = Array.isArray(detected?.columns)
    ? (detected.columns as string[])
    : [];

  const initialLabels = (log.column_overrides?.labels ?? {}) as Record<string, string>;
  const [labels, setLabels] = useState<Record<string, string>>(initialLabels);

  useEffect(() => {
    setLabels(initialLabels);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [log.id]);

  const dirty =
    JSON.stringify(labels) !== JSON.stringify(initialLabels);

  const onSave = async () => {
    const overrides: EventLogColumnOverrides = {
      ...(log.column_overrides ?? {}),
      labels: Object.fromEntries(
        Object.entries(labels).filter(([, v]) => v.trim().length > 0),
      ),
    };
    try {
      await update.mutateAsync({ column_overrides: overrides });
      toast.success("Schema updated");
    } catch (err) {
      toastError(`Save failed: ${(err as Error).message}`);
    }
  };

  return (
    <SectionShell
      title="Source & schema"
      description="Where this log came from and what's inside it."
    >
      <dl className="mb-5 grid gap-y-2 gap-x-6 text-sm sm:grid-cols-2">
        <DetailRow label="Source format" value={log.source_format ?? "–"} />
        <DetailRow label="Original filename" value={log.source_filename ?? "–"} />
        <DetailRow
          label="Imported"
          value={log.imported_at ? formatRelative(log.imported_at) : "–"}
        />
        <DetailRow label="Date range" value={formatDateRange(log.date_min, log.date_max)} />
      </dl>

      {columns.length > 0 ? (
        <>
          <div className="overflow-hidden rounded-md border">
            <Table>
              <TableHeader className="bg-muted/30">
                <TableRow>
                  <TableHead className="w-[28%]">Column</TableHead>
                  <TableHead>Display label</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {columns.map((col) => (
                  <TableRow key={col} className="h-10">
                    <TableCell className="font-mono text-xs">{col}</TableCell>
                    <TableCell>
                      <Input
                        className="h-8 text-sm"
                        value={labels[col] ?? ""}
                        placeholder={defaultLabel(col)}
                        onChange={(e) =>
                          setLabels((prev) => ({ ...prev, [col]: e.target.value }))
                        }
                      />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <div className="mt-3 flex justify-end">
            <Button
              size="sm"
              disabled={!dirty || update.isPending}
              onClick={onSave}
              className="cursor-pointer"
            >
              {update.isPending ? "Saving…" : "Save labels"}
            </Button>
          </div>
        </>
      ) : (
        <p className="text-sm italic text-muted-foreground">
          No schema was captured during import.
        </p>
      )}
    </SectionShell>
  );
}

function defaultLabel(col: string) {
  return col.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-3 border-b border-border/40 py-1 last:border-0">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="font-medium tabular-nums">{value}</dd>
    </div>
  );
}
