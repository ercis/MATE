"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, Search } from "lucide-react";

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
import { useVariants, type VariantsListParams } from "@/lib/queries";
import type { EventLogDetail } from "@/lib/api-types";
import { formatDuration, formatNumber, formatRelative } from "@/lib/format";
import { displayActivities, getActivityRenameMap } from "@/lib/activity-rename";
import { cn } from "@/lib/cn";

const PAGE_SIZE = 50;

type SortField = "case_count" | "avg_duration_seconds" | "last_seen";

interface SortState {
  field: SortField;
  dir: "asc" | "desc";
}

const SORT_LABEL: Record<SortField, string> = {
  case_count: "Cases",
  avg_duration_seconds: "Avg duration",
  last_seen: "Last seen",
};

export function VariantsTab({ logId, log }: { logId: string; log: EventLogDetail }) {
  const renameMap = useMemo(() => getActivityRenameMap(log), [log]);
  const [page, setPage] = useState(0);
  const [sort, setSort] = useState<SortState>({ field: "case_count", dir: "desc" });
  const [activityQuery, setActivityQuery] = useState("");
  const [minCases, setMinCases] = useState("");

  const params = useMemo<VariantsListParams>(
    () => ({
      offset: page * PAGE_SIZE,
      limit: PAGE_SIZE,
      sort: `${sort.field}:${sort.dir}`,
      activity_contains: activityQuery.trim() || undefined,
      min_case_count: minCases ? Number(minCases) : undefined,
    }),
    [page, sort, activityQuery, minCases],
  );

  const { data, isLoading, isError, error } = useVariants(logId, params);

  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  const onHeaderSort = (field: SortField) => {
    setSort((prev) => ({
      field,
      dir: prev.field === field && prev.dir === "desc" ? "asc" : "desc",
    }));
    setPage(0);
  };

  if (isError) {
    return (
      <div className="rounded-lg border border-destructive/20 bg-destructive/10 p-4 text-sm text-destructive">
        Could not load variants: {(error as Error)?.message ?? "Unknown error"}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative min-w-[220px]">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Variant contains activity…"
            className="pl-8 h-9"
            value={activityQuery}
            onChange={(e) => {
              setActivityQuery(e.target.value);
              setPage(0);
            }}
          />
        </div>
        <Input
          type="number"
          min={1}
          placeholder="Min cases"
          className="h-9 w-32"
          value={minCases}
          onChange={(e) => {
            setMinCases(e.target.value);
            setPage(0);
          }}
        />
      </div>

      <div className="rounded-lg border">
        {isLoading || !data ? (
          <div className="p-6 space-y-2">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-7 w-full" />
            ))}
          </div>
        ) : (
          <Table>
            <TableHeader className="bg-muted/30">
              <TableRow>
                <TableHead className="w-[60px] text-right text-xs uppercase tracking-wide text-muted-foreground">
                  #
                </TableHead>
                <TableHead className="w-[40%]">Variant</TableHead>
                <SortableHead
                  field="case_count"
                  label={SORT_LABEL.case_count}
                  align="right"
                  sort={sort}
                  onClick={onHeaderSort}
                />
                <TableHead className="w-[140px]">Share of cases</TableHead>
                <SortableHead
                  field="avg_duration_seconds"
                  label={SORT_LABEL.avg_duration_seconds}
                  align="right"
                  sort={sort}
                  onClick={onHeaderSort}
                />
                <SortableHead
                  field="last_seen"
                  label={SORT_LABEL.last_seen}
                  align="left"
                  sort={sort}
                  onClick={onHeaderSort}
                />
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.rows.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="h-32 text-center text-sm text-muted-foreground">
                    No variants match the current filters.
                  </TableCell>
                </TableRow>
              ) : (
                data.rows.map((v) => {
                  const display = displayActivities(v.activities, renameMap);
                  return (
                  <TableRow
                    key={v.variant_id}
                    className="h-12 cursor-pointer hover:bg-muted/40"
                  >
                    <TableCell className="text-right tabular-nums text-xs text-muted-foreground">
                      <Link
                        href={`/processes/${logId}/variants/${v.variant_id}`}
                        className="block"
                      >
                        {v.rank}
                      </Link>
                    </TableCell>
                    <TableCell>
                      <Link
                        href={`/processes/${logId}/variants/${v.variant_id}`}
                        className="block truncate hover:underline underline-offset-2"
                        title={display.join(" → ")}
                      >
                        {summarise(display)}
                      </Link>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatNumber(v.case_count)}
                    </TableCell>
                    <TableCell>
                      <CasePctBar pct={v.case_pct} />
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-sm">
                      {formatDuration(v.avg_duration_seconds)}
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {formatRelative(v.last_seen)}
                    </TableCell>
                  </TableRow>
                  );
                })
              )}
            </TableBody>
          </Table>
        )}
      </div>

      <div className="flex items-center justify-between text-sm text-muted-foreground">
        <span className="tabular-nums">
          {data ? `${formatNumber(data.total)} variants` : "–"}
        </span>
        <div className="flex items-center gap-2">
          <span className="tabular-nums">
            Page {page + 1} of {totalPages}
          </span>
          <Button
            variant="outline"
            size="icon"
            className="h-8 w-8 cursor-pointer"
            disabled={page === 0}
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            aria-label="Previous page"
          >
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <Button
            variant="outline"
            size="icon"
            className="h-8 w-8 cursor-pointer"
            disabled={page + 1 >= totalPages}
            onClick={() => setPage((p) => p + 1)}
            aria-label="Next page"
          >
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  );
}

function SortableHead({
  field,
  label,
  align,
  sort,
  onClick,
}: {
  field: SortField;
  label: string;
  align: "left" | "right";
  sort: SortState;
  onClick: (field: SortField) => void;
}) {
  const active = sort.field === field;
  return (
    <TableHead className={cn("whitespace-nowrap", align === "right" && "text-right")}>
      <button
        type="button"
        onClick={() => onClick(field)}
        className={cn(
          "inline-flex items-center gap-1 hover:text-foreground",
          align === "right" && "ml-auto",
        )}
      >
        {label}
        {active &&
          (sort.dir === "asc" ? (
            <ArrowUp className="h-3 w-3" />
          ) : (
            <ArrowDown className="h-3 w-3" />
          ))}
      </button>
    </TableHead>
  );
}

function CasePctBar({ pct }: { pct: number }) {
  const clamped = Math.max(0, Math.min(1, pct));
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
        <div
          className="h-full bg-primary"
          style={{ width: `${(clamped * 100).toFixed(1)}%` }}
        />
      </div>
      <span className="w-12 text-right text-xs tabular-nums text-muted-foreground">
        {(clamped * 100).toFixed(1)}%
      </span>
    </div>
  );
}

const MAX_ACTIVITIES = 5;
function summarise(activities: string[]): string {
  if (activities.length <= MAX_ACTIVITIES) return activities.join(" → ");
  const head = activities.slice(0, 2).join(" → ");
  const tail = activities.slice(-2).join(" → ");
  return `${head} → … (${activities.length - 4}) … → ${tail}`;
}
