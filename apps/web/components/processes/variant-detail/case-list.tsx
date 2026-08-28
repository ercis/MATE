"use client";

import Link from "next/link";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { VariantCase } from "@/lib/api-types";
import { formatDuration, formatNumber } from "@/lib/format";

export function CaseList({
  logId,
  cases,
  total,
  emptyLabel = "No cases recorded for this variant yet.",
}: {
  logId: string;
  cases: (VariantCase & { occurrences?: number })[];
  total: number;
  emptyLabel?: string;
}) {
  if (cases.length === 0) {
    return <p className="px-4 py-8 text-center text-sm text-muted-foreground">{emptyLabel}</p>;
  }
  // The activity-detail page feeds rows with a per-case occurrence count; the
  // variant page doesn't - the column only renders when the data carries it.
  const withOccurrences = cases.some((c) => typeof c.occurrences === "number");
  return (
    <>
      <Table>
        <TableHeader className="bg-muted/30">
          <TableRow>
            <TableHead>Case ID</TableHead>
            <TableHead>Start</TableHead>
            <TableHead>End</TableHead>
            <TableHead className="text-right">Duration</TableHead>
            <TableHead className="text-right">Events</TableHead>
            {withOccurrences && <TableHead className="text-right">Occurrences</TableHead>}
            <TableHead className="w-[120px] text-right" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {cases.map((c) => (
            <TableRow key={c.case_id} className="h-12">
              <TableCell className="font-mono text-sm">{c.case_id}</TableCell>
              <TableCell className="text-sm text-muted-foreground">
                {c.case_start ? new Date(c.case_start).toLocaleString() : "–"}
              </TableCell>
              <TableCell className="text-sm text-muted-foreground">
                {c.case_end ? new Date(c.case_end).toLocaleString() : "–"}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {formatDuration(c.case_duration_seconds)}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {formatNumber(c.event_count)}
              </TableCell>
              {withOccurrences && (
                <TableCell className="text-right tabular-nums">
                  {formatNumber(c.occurrences ?? 0)}
                </TableCell>
              )}
              <TableCell className="text-right">
                <Link
                  href={`/processes/${logId}?tab=events&case_id=${encodeURIComponent(c.case_id)}`}
                  className="text-xs text-primary hover:underline underline-offset-2"
                >
                  Show events →
                </Link>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {total > cases.length && (
        <p className="border-t px-4 py-2 text-xs text-muted-foreground">
          Showing the first {cases.length} of {total} cases.
        </p>
      )}
    </>
  );
}
