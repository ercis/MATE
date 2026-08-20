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
import { Skeleton } from "@/components/ui/skeleton";
import { useDataQuality } from "@/lib/queries";
import { formatNumber } from "@/lib/format";
import { cn } from "@/lib/cn";

import { SectionShell } from "./general-section";

export function QualitySection({ logId }: { logId: string }) {
  const { data, isLoading } = useDataQuality(logId);

  return (
    <SectionShell
      title="Data quality"
      description="Per-column completeness - click a row to see only events with missing values for that column."
    >
      {isLoading || !data ? (
        <div className="space-y-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-7 w-full" />
          ))}
        </div>
      ) : data.columns.length === 0 ? (
        <p className="text-sm italic text-muted-foreground">No data to assess.</p>
      ) : (
        <div className="overflow-hidden rounded-md border">
          <Table>
            <TableHeader className="bg-muted/30">
              <TableRow>
                <TableHead>Column</TableHead>
                <TableHead className="text-right">Missing</TableHead>
                <TableHead className="w-[180px]">Coverage</TableHead>
                <TableHead className="text-right">Distinct</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.columns.map((col) => {
                const filled = Math.max(0, data.total_events - col.null_count);
                const filledPct = data.total_events
                  ? (filled / data.total_events) * 100
                  : 100;
                const hasGaps = col.null_count > 0;
                const isRequired =
                  col.role === "case_id" || col.role === "activity" || col.role === "timestamp";
                return (
                  <TableRow
                    key={col.column}
                    className={cn("h-10", hasGaps && "bg-amber-500/5")}
                  >
                    <TableCell>
                      <span className="font-medium">{col.label}</span>
                      <span className="ml-2 font-mono text-[11px] text-muted-foreground">
                        {col.column}
                      </span>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatNumber(col.null_count)}
                      <span className="ml-1 text-xs text-muted-foreground">
                        ({(col.null_pct * 100).toFixed(1)}%)
                      </span>
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
                          <div
                            className={cn(
                              "h-full",
                              filledPct >= 99 ? "bg-emerald-500" : "bg-amber-500",
                            )}
                            style={{ width: `${filledPct.toFixed(1)}%` }}
                          />
                        </div>
                      </div>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatNumber(col.distinct_count)}
                    </TableCell>
                    <TableCell className="text-right">
                      {hasGaps && isRequired && (
                        <Link
                          href={`/processes/${logId}?tab=events&missing_only=true`}
                          className="text-xs text-primary hover:underline underline-offset-2"
                        >
                          Show missing →
                        </Link>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}
    </SectionShell>
  );
}
