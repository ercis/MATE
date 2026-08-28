"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Check, ChevronDown, Loader2, Search } from "lucide-react";

import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useColumnValues } from "@/lib/queries";
import { formatNumber, toLocalInputString, toNaiveUtcString } from "@/lib/format";
import { cn } from "@/lib/cn";
import type { ColumnSpec, FilterEntry, FilterOp } from "@/lib/api-types";

/** A persisted datetime filter value is a naive-UTC literal (what DuckDB
 * compares against the stored naive-UTC column). The datetime-local input is
 * local wall-clock, so convert when seeding it. */
function toDatetimeInputValue(raw: unknown): string {
  const s = String(raw);
  // Naive literal = UTC components; suffix it so Date.parse reads it as UTC.
  const ms = Date.parse(/(Z|[+-]\d\d:?\d\d)$/.test(s) ? s : `${s}Z`);
  return Number.isNaN(ms) ? s : toLocalInputString(ms);
}

// Single-value operators offered in the "Advanced" section. The Excel-style
// value checklist above them covers the common "pick these values" case via
// the `in` op.
const OPS_BY_TYPE: Record<ColumnSpec["type"], FilterOp[]> = {
  string: ["contains", "equals", "is_null", "is_not_null"],
  number: ["equals", "gte", "lte", "is_null", "is_not_null"],
  duration: ["equals", "gte", "lte", "is_null", "is_not_null"],
  datetime: ["equals", "gte", "lte", "is_null", "is_not_null"],
  enum: ["equals", "is_null", "is_not_null"],
  boolean: ["equals", "is_null", "is_not_null"],
};

const OP_LABELS: Record<FilterOp, string> = {
  contains: "contains",
  equals: "equals",
  gte: "≥",
  lte: "≤",
  is_null: "is empty",
  is_not_null: "is not empty",
  in: "is any of",
};

export interface ColumnFilterProps {
  logId: string;
  column: ColumnSpec;
  current: FilterEntry | null;
  onChange: (next: FilterEntry | null) => void;
  children: ReactNode;
  /** Start with the editor popover open – used by the dashboard filter bar so
   * a freshly-added column prompts for its value immediately. */
  defaultOpen?: boolean;
}

export function ColumnFilter({
  logId,
  column,
  current,
  onChange,
  children,
  defaultOpen = false,
}: ColumnFilterProps) {
  const ops = OPS_BY_TYPE[column.type] ?? OPS_BY_TYPE.string;
  const [open, setOpen] = useState(defaultOpen);

  // ── value checklist state ────────────────────────────────────────────────
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  // `checked === null` means "no checklist restriction" (all values). Once the
  // user touches the list it becomes a concrete set of the values to keep.
  const [checked, setChecked] = useState<Set<string> | null>(
    current?.op === "in" && Array.isArray(current.value)
      ? new Set(current.value.map(String))
      : null,
  );

  // ── advanced operator state ──────────────────────────────────────────────
  const advancedActive = !!current && current.op !== "in";
  const [showAdvanced, setShowAdvanced] = useState(advancedActive);
  const [op, setOp] = useState<FilterOp>(advancedActive ? current!.op : ops[0]);
  const [opValue, setOpValue] = useState<string>(
    advancedActive && current!.value !== undefined && current!.value !== null
      ? column.type === "datetime"
        ? toDatetimeInputValue(current!.value)
        : String(current!.value)
      : "",
  );

  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search.trim()), 200);
    return () => clearTimeout(t);
  }, [search]);

  const { data, isFetching } = useColumnValues(
    logId,
    column.name,
    debouncedSearch,
    open,
  );
  const values = data?.values ?? [];

  // Default the checklist to "everything selected" the first time the full
  // (unsearched) value list arrives, unless we already inherited an `in` set.
  useEffect(() => {
    if (!open) return;
    if (checked !== null) return;
    if (debouncedSearch) return;
    if (!data) return;
    setChecked(new Set(data.values.map((v) => v.value)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, data, debouncedSearch]);

  const toggleValue = (value: string) => {
    setChecked((prev) => {
      const next = new Set(prev ?? values.map((v) => v.value));
      if (next.has(value)) next.delete(value);
      else next.add(value);
      return next;
    });
  };

  const selectAllVisible = () => {
    setChecked((prev) => {
      const next = new Set(prev ?? []);
      for (const v of values) next.add(v.value);
      return next;
    });
  };

  const clearAllVisible = () => {
    setChecked((prev) => {
      const next = new Set(prev ?? values.map((v) => v.value));
      for (const v of values) next.delete(v.value);
      return next;
    });
  };

  const checkedCount = checked?.size ?? values.length;
  const allVisibleChecked =
    values.length > 0 && values.every((v) => checked?.has(v.value) ?? true);

  const apply = () => {
    // Advanced operator takes precedence when the user has opened it and set
    // something meaningful.
    if (showAdvanced) {
      if (op === "is_null" || op === "is_not_null") {
        onChange({ field: column.name, op });
        setOpen(false);
        return;
      }
      if (opValue !== "") {
        onChange({ field: column.name, op, value: castValue(opValue, column) });
        setOpen(false);
        return;
      }
    }
    // Otherwise fall back to the value checklist.
    if (checked === null) {
      // Never touched – no restriction.
      onChange(null);
    } else if (checked.size === 0) {
      onChange({ field: column.name, op: "in", value: [] });
    } else if (!debouncedSearch && !data?.truncated && allVisibleChecked) {
      // Every (loaded) value is selected and nothing is hidden → no filter.
      onChange(null);
    } else {
      onChange({ field: column.name, op: "in", value: [...checked] });
    }
    setOpen(false);
  };

  const clear = () => {
    onChange(null);
    setChecked(null);
    setSearch("");
    setOp(ops[0]);
    setOpValue("");
    setShowAdvanced(false);
    setOpen(false);
  };

  const showValueInput = op !== "is_null" && op !== "is_not_null";
  const inputType =
    column.type === "datetime"
      ? "datetime-local"
      : column.type === "number" || column.type === "duration"
        ? "number"
        : "text";

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>{children}</PopoverTrigger>
      <PopoverContent align="start" className="w-80 p-0">
        <div className="space-y-2 p-3">
          <Label className="text-xs text-muted-foreground">
            Filter {column.label}
          </Label>

          {/* Value checklist */}
          <div className="relative">
            <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              placeholder="Search values…"
              className="h-8 pl-7 text-sm"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            {isFetching && (
              <Loader2 className="absolute right-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 animate-spin text-muted-foreground" />
            )}
          </div>

          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span className="tabular-nums">{checkedCount} selected</span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                className="hover:text-foreground"
                onClick={selectAllVisible}
              >
                Select all
              </button>
              <span>·</span>
              <button
                type="button"
                className="hover:text-foreground"
                onClick={clearAllVisible}
              >
                Clear
              </button>
            </div>
          </div>

          <ScrollArea className="h-48 rounded-md border">
            <div className="p-1">
              {values.length === 0 ? (
                <div className="px-2 py-6 text-center text-xs text-muted-foreground">
                  {isFetching ? "Loading…" : "No values"}
                </div>
              ) : (
                values.map((v) => {
                  const isChecked = checked?.has(v.value) ?? true;
                  return (
                    <button
                      key={v.value}
                      type="button"
                      onClick={() => toggleValue(v.value)}
                      className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm hover:bg-muted"
                    >
                      <span
                        className={cn(
                          "flex h-4 w-4 shrink-0 items-center justify-center rounded border",
                          isChecked
                            ? "border-primary bg-primary text-primary-foreground"
                            : "border-input",
                        )}
                      >
                        {isChecked && <Check className="h-3 w-3" />}
                      </span>
                      <span className="flex-1 truncate" title={v.value}>
                        {v.value}
                      </span>
                      <span className="shrink-0 tabular-nums text-xs text-muted-foreground">
                        {formatNumber(v.count)}
                      </span>
                    </button>
                  );
                })
              )}
            </div>
          </ScrollArea>
          {data?.truncated && (
            <p className="text-xs text-amber-600 dark:text-amber-400">
              Showing the {formatNumber(values.length)} most common of{" "}
              {formatNumber(data.total_distinct)} values - search to narrow.
            </p>
          )}

          {/* Advanced operators */}
          <button
            type="button"
            onClick={() => setShowAdvanced((v) => !v)}
            className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            <ChevronDown
              className={cn("h-3 w-3 transition-transform", showAdvanced && "rotate-180")}
            />
            Advanced
          </button>
          {showAdvanced && (
            <div className="grid grid-cols-2 gap-2">
              <Select value={op} onValueChange={(v) => setOp(v as FilterOp)}>
                <SelectTrigger className="h-8 text-sm">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {ops.map((o) => (
                    <SelectItem key={o} value={o}>
                      {OP_LABELS[o]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {showValueInput &&
                (column.type === "enum" && column.enum_values ? (
                  <Select value={opValue} onValueChange={setOpValue}>
                    <SelectTrigger className="h-8 text-sm">
                      <SelectValue placeholder="Pick…" />
                    </SelectTrigger>
                    <SelectContent>
                      {column.enum_values.map((v) => (
                        <SelectItem key={v} value={v}>
                          {v}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                ) : (
                  <Input
                    type={inputType}
                    value={opValue}
                    onChange={(e) => setOpValue(e.target.value)}
                    className="h-8 text-sm"
                  />
                ))}
            </div>
          )}
        </div>

        <Separator />
        <div className="flex justify-between p-2">
          <Button variant="ghost" size="sm" onClick={clear} className="cursor-pointer">
            Clear
          </Button>
          <Button size="sm" onClick={apply} className="cursor-pointer">
            Apply
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}

function castValue(raw: string, column: ColumnSpec): string | number | boolean | null {
  if (column.type === "number" || column.type === "duration") {
    const n = Number(raw);
    return Number.isFinite(n) ? n : raw;
  }
  if (column.type === "boolean") return raw === "true";
  if (column.type === "datetime") {
    // datetime-local gives *local* wall-clock; the stored column is naive UTC,
    // so send the instant's UTC components (inverse of toDatetimeInputValue).
    const ms = Date.parse(raw);
    return Number.isNaN(ms) ? raw : toNaiveUtcString(ms);
  }
  return raw;
}
