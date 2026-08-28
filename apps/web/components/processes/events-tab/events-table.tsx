"use client";

import { useCallback, useMemo } from "react";
import { ArrowDown, ArrowUp, ArrowUpDown, Filter as FilterIcon } from "lucide-react";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/cn";
import type { ColumnSpec, EventRow, EventsPage, FilterEntry } from "@/lib/api-types";
import type { ActivityRenameMap } from "@/lib/activity-rename";

import { CellEditor } from "./cell-editor";
import { ColumnFilter } from "./column-filter";

export interface EventsTableProps {
  logId: string;
  page: EventsPage;
  sort: string;
  filters: FilterEntry[];
  editMode: boolean;
  activityRenames: ActivityRenameMap;
  onSortChange: (next: string) => void;
  onFiltersChange: (next: FilterEntry[]) => void;
}

interface SortInfo {
  field: string | null;
  dir: "asc" | "desc" | null;
}

function parseSort(raw: string): SortInfo {
  if (!raw) return { field: null, dir: null };
  const [field, dir] = raw.split(":");
  if (dir !== "asc" && dir !== "desc") return { field: null, dir: null };
  return { field, dir };
}

function nextSort(current: SortInfo, field: string): string {
  if (current.field !== field) return `${field}:asc`;
  if (current.dir === "asc") return `${field}:desc`;
  return ""; // third click clears the sort
}

export function EventsTable({
  logId,
  page,
  sort,
  filters,
  editMode,
  activityRenames,
  onSortChange,
  onFiltersChange,
}: EventsTableProps) {
  const sortInfo = parseSort(sort);
  const visibleColumns = useMemo(
    () => page.columns.filter((c) => !c.name.startsWith("_")),
    [page.columns],
  );

  const filterByField = useMemo(() => {
    const map = new Map<string, FilterEntry>();
    for (const f of filters) map.set(f.field, f);
    return map;
  }, [filters]);

  const setColumnFilter = useCallback(
    (field: string, next: FilterEntry | null) => {
      const without = filters.filter((f) => f.field !== field);
      onFiltersChange(next ? [...without, next] : without);
    },
    [filters, onFiltersChange],
  );

  return (
    <Table>
      <TableHeader className="bg-muted/30">
        <TableRow>
          <TableHead className="w-[60px] text-right text-xs uppercase tracking-wide text-muted-foreground">
            #
          </TableHead>
          {visibleColumns.map((col) => (
            <TableHead key={col.name} className="whitespace-nowrap">
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  className="flex items-center gap-1 hover:text-foreground"
                  onClick={() => onSortChange(nextSort(sortInfo, col.name))}
                  title={`Sort by ${col.label}`}
                >
                  <span>{col.label}</span>
                  {col.required && <span className="text-amber-500">*</span>}
                  {sortInfo.field === col.name ? (
                    sortInfo.dir === "asc" ? (
                      <ArrowUp className="h-3 w-3" />
                    ) : (
                      <ArrowDown className="h-3 w-3" />
                    )
                  ) : (
                    <ArrowUpDown className="h-3 w-3 text-muted-foreground/40" />
                  )}
                </button>
                <ColumnFilter
                  logId={logId}
                  column={col}
                  current={filterByField.get(col.name) ?? null}
                  onChange={(next) => setColumnFilter(col.name, next)}
                >
                  <button
                    type="button"
                    className={cn(
                      "rounded p-0.5 hover:bg-muted",
                      filterByField.has(col.name) && "text-primary",
                    )}
                    title={`Filter ${col.label}`}
                  >
                    <FilterIcon className="h-3 w-3" />
                  </button>
                </ColumnFilter>
              </div>
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {page.rows.length === 0 ? (
          <TableRow>
            <TableCell
              colSpan={visibleColumns.length + 1}
              className="h-32 text-center text-sm text-muted-foreground"
            >
              No events match the current filters.
            </TableCell>
          </TableRow>
        ) : (
          page.rows.map((row, idx) => (
            <EventTableRow
              key={`${page.offset}-${idx}`}
              logId={logId}
              row={row}
              rowIndex={page.offset + idx}
              displayIndex={page.offset + idx + 1}
              columns={visibleColumns}
              editMode={editMode}
              activityRenames={activityRenames}
            />
          ))
        )}
      </TableBody>
    </Table>
  );
}

function EventTableRow({
  logId,
  row,
  rowIndex,
  displayIndex,
  columns,
  editMode,
  activityRenames,
}: {
  logId: string;
  row: EventRow;
  rowIndex: number;
  displayIndex: number;
  columns: ColumnSpec[];
  editMode: boolean;
  activityRenames: ActivityRenameMap;
}) {
  const hasMissing = !!row._has_missing;
  return (
    <TableRow
      className={cn(
        "h-12",
        hasMissing && "bg-amber-500/5",
      )}
    >
      <TableCell className="text-right tabular-nums text-xs text-muted-foreground">
        {displayIndex}
      </TableCell>
      {columns.map((col) => {
        const value = row[col.name];
        return (
          <TableCell
            key={col.name}
            className={cn(
              hasMissing && col.required && (value === null || value === undefined) && "text-amber-600 dark:text-amber-400",
            )}
          >
            <CellEditor
              logId={logId}
              rowIndex={rowIndex}
              column={col}
              value={value}
              editMode={editMode}
              displayOverride={
                col.role === "activity" && typeof value === "string"
                  ? activityRenames[value]
                  : undefined
              }
            />
          </TableCell>
        );
      })}
    </TableRow>
  );
}
