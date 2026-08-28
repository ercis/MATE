"use client";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatNumber } from "@/lib/format";
import type { ColumnType, VizComponentProps } from "@/lib/visualizations/types";
import { VizEmpty } from "@/components/visualizations/viz-shell";
import { asTable } from "@/components/visualizations/charts/chart-common";

const MAX_ROWS = 200;

function cell(value: unknown, type: ColumnType): string {
  if (value == null) return "";
  if ((type === "number" || type === "integer") && typeof value === "number") {
    return formatNumber(value);
  }
  return String(value);
}

/** Plain typed table - renders any `table`-shaped dataset, capped for size. */
export function TableViz({ dataset }: VizComponentProps) {
  const table = asTable(dataset);
  if (table.columns.length === 0 || table.rows.length === 0) {
    return <VizEmpty message={dataset.meta?.note} />;
  }
  const rows = table.rows.slice(0, MAX_ROWS);
  return (
    <div className="h-full w-full overflow-auto">
      <Table>
        <TableHeader className="sticky top-0 z-[1] bg-card">
          <TableRow>
            {table.columns.map((c) => (
              <TableHead key={c.id} className="h-8 whitespace-nowrap text-xs">
                {c.label}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((r, i) => (
            <TableRow key={i}>
              {table.columns.map((c) => (
                <TableCell key={c.id} className="whitespace-nowrap py-1.5 text-xs tabular-nums">
                  {cell(r[c.id], c.type)}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {table.rows.length > rows.length && (
        <p className="p-2 text-center text-[11px] text-muted-foreground">
          Showing {rows.length} of {formatNumber(table.rows.length)} rows
        </p>
      )}
    </div>
  );
}
