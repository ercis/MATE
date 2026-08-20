"use client";

import { useMemo, useState } from "react";
import { Filter, Plus, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { ColumnFilter } from "@/components/processes/events-tab/column-filter";
import { cn } from "@/lib/cn";
import type { ColumnSpec, FilterEntry, FilterOp } from "@/lib/api-types";

const OP_LABEL: Record<FilterOp, string> = {
  contains: "contains",
  equals: "=",
  gte: "≥",
  lte: "≤",
  is_null: "is empty",
  is_not_null: "is not empty",
  in: "is any of",
};

function summarize(f: FilterEntry): string {
  if (f.op === "is_null" || f.op === "is_not_null") return OP_LABEL[f.op];
  if (f.op === "in" && Array.isArray(f.value)) {
    const vals = f.value;
    return vals.length <= 2 ? `is ${vals.join(", ")}` : `is any of ${vals.length}`;
  }
  return `${OP_LABEL[f.op]} ${f.value ?? ""}`.trim();
}

/**
 * The dashboard's global column-filter bar. Reuses the Events tab's
 * `ColumnFilter` editor so the add/remove/value UX is identical. Active filters
 * render as editable chips; "Add filter" picks an unfiltered column and opens
 * its editor straight away. Commits flow up to the `DashboardFilterProvider`,
 * which replaces the committed log filter for every widget on this dashboard.
 */
export function DashboardFilterBar({
  logId,
  columns,
  filters,
  onChange,
}: {
  logId: string;
  columns: ColumnSpec[];
  filters: FilterEntry[];
  onChange: (next: FilterEntry[]) => void;
}) {
  const [editingField, setEditingField] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [search, setSearch] = useState("");

  const colByName = useMemo(() => new Map(columns.map((c) => [c.name, c])), [columns]);
  const activeFields = useMemo(() => new Set(filters.map((f) => f.field)), [filters]);

  const setColumnFilter = (field: string, next: FilterEntry | null) => {
    const without = filters.filter((f) => f.field !== field);
    onChange(next ? [...without, next] : without);
  };

  const available = useMemo(() => {
    const q = search.trim().toLowerCase();
    return columns.filter(
      (c) => !activeFields.has(c.name) && (!q || c.label.toLowerCase().includes(q)),
    );
  }, [columns, activeFields, search]);

  return (
    <div className="flex flex-wrap items-center gap-1.5 border-b border-border bg-card/40 px-3 py-2">
      <Filter className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />

      {filters.length === 0 && !editingField && (
        <span className="text-xs text-muted-foreground">No filters - showing the full log.</span>
      )}

      {filters.map((f) => {
        const col = colByName.get(f.field);
        if (!col) return null;
        return (
          <div
            key={f.field}
            className="flex items-center overflow-hidden rounded-md border border-border bg-background text-xs"
          >
            <ColumnFilter
              logId={logId}
              column={col}
              current={f}
              onChange={(next) => setColumnFilter(f.field, next)}
            >
              <button type="button" className="px-2 py-1 hover:bg-muted">
                <span className="font-medium">{col.label}</span>{" "}
                <span className="text-muted-foreground">{summarize(f)}</span>
              </button>
            </ColumnFilter>
            <button
              type="button"
              aria-label={`Remove ${col.label} filter`}
              className="border-l border-border px-1 py-1 text-muted-foreground hover:bg-muted hover:text-destructive"
              onClick={() => setColumnFilter(f.field, null)}
            >
              <X className="h-3 w-3" />
            </button>
          </div>
        );
      })}

      {/* Freshly-added column: render its editor open immediately. */}
      {editingField && colByName.get(editingField) && !activeFields.has(editingField) && (
        <ColumnFilter
          key={editingField}
          logId={logId}
          column={colByName.get(editingField)!}
          current={null}
          defaultOpen
          onChange={(next) => {
            setColumnFilter(editingField, next);
            setEditingField(null);
          }}
        >
          <button
            type="button"
            className="rounded-md border border-dashed border-primary/60 px-2 py-1 text-xs text-primary"
          >
            {colByName.get(editingField)!.label}…
          </button>
        </ColumnFilter>
      )}

      <Popover open={addOpen} onOpenChange={setAddOpen}>
        <PopoverTrigger asChild>
          <Button type="button" variant="ghost" size="sm" className="h-7 gap-1 px-2 text-xs">
            <Plus className="h-3 w-3" />
            Add filter
          </Button>
        </PopoverTrigger>
        <PopoverContent align="start" className="w-60 p-0">
          <div className="p-2">
            <Input
              autoFocus
              placeholder="Search columns…"
              className="h-8 text-sm"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <div className="max-h-64 overflow-y-auto">
            <div className="p-1">
              {available.length === 0 ? (
                <div className="px-2 py-6 text-center text-xs text-muted-foreground">
                  No columns
                </div>
              ) : (
                available.map((c) => (
                  <button
                    key={c.name}
                    type="button"
                    className={cn(
                      "flex w-full items-center justify-between rounded px-2 py-1.5 text-left text-sm hover:bg-muted",
                    )}
                    onClick={() => {
                      setEditingField(c.name);
                      setAddOpen(false);
                      setSearch("");
                    }}
                  >
                    <span className="truncate">{c.label}</span>
                    <span className="shrink-0 text-xs text-muted-foreground">{c.type}</span>
                  </button>
                ))
              )}
            </div>
          </div>
        </PopoverContent>
      </Popover>

      {filters.length > 0 && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="ml-auto h-7 px-2 text-xs text-muted-foreground"
          onClick={() => onChange([])}
        >
          Clear all
        </Button>
      )}
    </div>
  );
}
