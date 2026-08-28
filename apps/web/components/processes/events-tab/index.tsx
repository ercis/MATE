"use client";

import { useCallback, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  ChevronLeft,
  ChevronRight,
  Loader2,
  Pencil,
  RotateCcw,
  Search,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  useApplyActiveFilter,
  useEventLogRows,
  type EventsListParams,
} from "@/lib/queries";
import type { EventLogDetail, FilterEntry } from "@/lib/api-types";
import { formatNumber } from "@/lib/format";
import { getActivityRenameMap } from "@/lib/activity-rename";
import { cn } from "@/lib/cn";
import { PROCESS_PAGE_SIZE } from "@/lib/query-keys";
import { useDebouncedValue } from "@/lib/use-debounced-value";

import { EventsTable } from "./events-table";

const PAGE_SIZES = [25, 50, 100, 200];
// Shared with prefetchProcessTabs (client-prefetch.ts): the prefetched first
// page must hash to the same query key this tab reads on first render.
const DEFAULT_PAGE_SIZE = PROCESS_PAGE_SIZE;

export function EventsTab({ logId, log }: { logId: string; log: EventLogDetail }) {
  const activityRenames = useMemo(() => getActivityRenameMap(log), [log]);
  const router = useRouter();
  const searchParams = useSearchParams();

  const caseIdFilter = searchParams.get("case_id");
  const missingOnlyParam = searchParams.get("missing_only") === "true";

  const [page, setPage] = useState(0);
  const [limit, setLimit] = useState(DEFAULT_PAGE_SIZE);
  const [sort, setSort] = useState<string>("");
  // The editor draft is seeded from the *applied* filter so the user lands on
  // what's currently in force and can refine it.
  const [filters, setFilters] = useState<FilterEntry[]>(() => log.active_filter ?? []);
  const [q, setQ] = useState("");
  const [missingOnly, setMissingOnly] = useState(missingOnlyParam);
  const [editMode, setEditMode] = useState(false);

  const applyFilter = useApplyActiveFilter(logId);
  const isDirty = normalizeFilters(filters) !== normalizeFilters(log.active_filter);
  const hasApplied = (log.active_filter?.length ?? 0) > 0;

  // Debounced: the free-text search fans out to a CAST+ILIKE across every
  // column on the API - per-keystroke requests would full-scan the log.
  const debouncedQ = useDebouncedValue(q, 300);

  const params = useMemo<EventsListParams>(
    () => ({
      offset: page * limit,
      limit,
      sort: sort || undefined,
      filter: filters.length > 0 ? filters : undefined,
      q: debouncedQ.trim() || undefined,
      missing_only: missingOnly || undefined,
      case_id: caseIdFilter ?? undefined,
    }),
    [page, limit, sort, filters, debouncedQ, missingOnly, caseIdFilter],
  );

  const { data, isLoading, isError, error, isFetching } = useEventLogRows(logId, params);

  const totalPages = data ? Math.max(1, Math.ceil(data.total / limit)) : 1;
  const start = data ? page * limit + 1 : 0;
  const end = data ? Math.min(data.total, start + data.rows.length - 1) : 0;
  const missingRows = data?.rows.filter((r) => r._has_missing).length ?? 0;

  const clearCaseIdFilter = useCallback(() => {
    const sp = new URLSearchParams(searchParams.toString());
    sp.delete("case_id");
    router.replace(`?${sp.toString() || "tab=events"}`, { scroll: false });
  }, [router, searchParams]);

  const onSearchChange = useCallback((value: string) => {
    setQ(value);
    setPage(0);
  }, []);

  const onMissingOnlyChange = useCallback((checked: boolean) => {
    setMissingOnly(checked);
    setPage(0);
  }, []);

  const onSortChange = useCallback((next: string) => {
    setSort(next);
    setPage(0);
  }, []);

  const onFiltersChange = useCallback((next: FilterEntry[]) => {
    setFilters(next);
    setPage(0);
  }, []);

  const onApply = useCallback(() => {
    applyFilter.mutate(filters, {
      onSuccess: () =>
        toast.success(
          filters.length === 0
            ? "Filter cleared - modules reprocessing the full dataset."
            : "Filter applied - modules reprocessing the filtered dataset.",
        ),
      onError: (e) => toast.error((e as Error)?.message ?? "Could not apply filter."),
    });
  }, [applyFilter, filters]);

  const onRestore = useCallback(() => {
    setFilters([]);
    setPage(0);
    applyFilter.mutate([], {
      onSuccess: () =>
        toast.success("Filter cleared - modules reprocessing the full dataset."),
      onError: (e) => toast.error((e as Error)?.message ?? "Could not clear filter."),
    });
  }, [applyFilter]);

  if (isError) {
    return (
      <div className="rounded-lg border border-destructive/20 bg-destructive/10 p-4 text-sm text-destructive">
        Could not load events: {(error as Error)?.message ?? "Unknown error"}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[220px] max-w-md">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Search across all columns…"
            className="pl-8 h-9"
            value={q}
            onChange={(e) => onSearchChange(e.target.value)}
          />
        </div>
        <label className="flex items-center gap-2 text-sm text-muted-foreground">
          <Switch checked={missingOnly} onCheckedChange={onMissingOnlyChange} />
          Only rows with missing values
        </label>
        {caseIdFilter && (
          <button
            type="button"
            onClick={clearCaseIdFilter}
            className="rounded-md border border-border bg-card px-2 py-1 text-xs hover:bg-muted"
          >
            case_id = {caseIdFilter} ✕
          </button>
        )}
        <div className="ml-auto flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            className="h-9 cursor-pointer"
            disabled={(!hasApplied && filters.length === 0) || applyFilter.isPending}
            onClick={onRestore}
            title="Clear the applied filter and reprocess every module on the full dataset"
          >
            <RotateCcw className="mr-1.5 h-3.5 w-3.5" />
            Restore
          </Button>
          <Button
            variant={isDirty ? "default" : "outline"}
            size="sm"
            className="h-9 cursor-pointer"
            disabled={!isDirty || applyFilter.isPending}
            onClick={onApply}
            title="Apply the filter to every module (re-runs all import processes)"
          >
            {applyFilter.isPending ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : null}
            Apply{isDirty ? " •" : ""}
          </Button>
          <Button
            variant={editMode ? "default" : "outline"}
            size="sm"
            className="h-9 cursor-pointer"
            onClick={() => setEditMode((v) => !v)}
          >
            <Pencil className="mr-1.5 h-3.5 w-3.5" />
            {editMode ? "Editing" : "Edit"}
          </Button>
        </div>
      </div>

      {hasApplied && (
        <div className="rounded-md border border-primary/30 bg-primary/5 px-3 py-2 text-sm text-muted-foreground">
          An applied filter is active across all modules and analytics
          {isDirty ? " - you have unapplied changes." : "."}
        </div>
      )}

      {missingRows > 0 && !missingOnly && (
        <div className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-900 dark:text-amber-100">
          {missingRows} of these rows have missing required values.{" "}
          <button
            type="button"
            className="underline underline-offset-2 hover:no-underline"
            onClick={() => onMissingOnlyChange(true)}
          >
            Show only those
          </button>
          .
        </div>
      )}

      <div className={cn("rounded-lg border", isFetching && "opacity-90")}>
        {isLoading || !data ? (
          <div className="p-6 space-y-2">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-6 w-full" />
            ))}
          </div>
        ) : (
          <EventsTable
            logId={logId}
            page={data}
            sort={sort}
            filters={filters}
            editMode={editMode}
            activityRenames={activityRenames}
            onSortChange={onSortChange}
            onFiltersChange={onFiltersChange}
          />
        )}
      </div>

      <div className="flex items-center justify-between text-sm text-muted-foreground">
        <div>
          {data ? (
            <span className="tabular-nums">
              {formatNumber(start)}–{formatNumber(end)} of {formatNumber(data.total)}
            </span>
          ) : (
            <span>–</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <span>Rows per page</span>
          <Select
            value={String(limit)}
            onValueChange={(v) => {
              setLimit(Number(v));
              setPage(0);
            }}
          >
            <SelectTrigger className="h-8 w-[80px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {PAGE_SIZES.map((s) => (
                <SelectItem key={s} value={String(s)}>
                  {s}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <span className="ml-2 tabular-nums">
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

/** Order-independent serialization so we can compare the editor draft against
 * the applied filter (field order and `in` value order don't matter). */
function normalizeFilters(filters: FilterEntry[] | null | undefined): string {
  const list = (filters ?? [])
    .map((f) => ({
      field: f.field,
      op: f.op,
      value: Array.isArray(f.value)
        ? [...f.value].map(String).sort()
        : (f.value ?? null),
    }))
    .sort((a, b) => a.field.localeCompare(b.field) || a.op.localeCompare(b.op));
  return JSON.stringify(list);
}
