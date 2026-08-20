"use client";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { formatNumber } from "@/lib/format";
import type { OcelPage } from "@/lib/api-types";

export interface OcelDataTableProps {
  page: OcelPage | undefined;
  isLoading: boolean;
  isError: boolean;
  error?: unknown;
  offset: number;
  limit: number;
  onOffsetChange: (next: number) => void;
  emptyLabel?: string;
}

function renderCell(value: unknown): string {
  if (value === null || value === undefined) return "–";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/** Generic paged table over an OCEL page response (objects / events /
 * relationships). Columns are taken straight from the parquet schema. */
export function OcelDataTable({
  page,
  isLoading,
  isError,
  error,
  offset,
  limit,
  onOffsetChange,
  emptyLabel = "No rows.",
}: OcelDataTableProps) {
  if (isError) {
    return (
      <div className="rounded-lg border border-destructive/20 bg-destructive/10 p-4 text-sm text-destructive">
        Could not load data: {(error as Error)?.message ?? "Unknown error"}
      </div>
    );
  }

  if (isLoading || !page) {
    return (
      <div className="rounded-lg border p-6 space-y-2">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-7 w-full" />
        ))}
      </div>
    );
  }

  const from = page.total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + limit, page.total);

  return (
    <div className="space-y-3">
      <div className="rounded-lg border overflow-x-auto">
        {page.rows.length === 0 ? (
          <p className="px-4 py-8 text-center text-sm text-muted-foreground">{emptyLabel}</p>
        ) : (
          <Table>
            <TableHeader className="bg-muted/30">
              <TableRow>
                {page.columns.map((col) => (
                  <TableHead key={col} className="whitespace-nowrap font-mono text-xs">
                    {col}
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {page.rows.map((row, i) => (
                <TableRow key={i} className="h-11">
                  {page.columns.map((col) => (
                    <TableCell key={col} className="whitespace-nowrap text-xs">
                      {renderCell(row[col])}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>

      <div className="flex items-center justify-between text-sm text-muted-foreground">
        <span>
          <span className="tabular-nums">{formatNumber(from)}</span>–
          <span className="tabular-nums">{formatNumber(to)}</span> of{" "}
          <span className="tabular-nums">{formatNumber(page.total)}</span>
        </span>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            className="cursor-pointer"
            disabled={offset === 0}
            onClick={() => onOffsetChange(Math.max(0, offset - limit))}
          >
            Previous
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="cursor-pointer"
            disabled={to >= page.total}
            onClick={() => onOffsetChange(offset + limit)}
          >
            Next
          </Button>
        </div>
      </div>
    </div>
  );
}
