"use client";

import { useEffect, useMemo, useState } from "react";
import { CalendarRange, Check, RotateCcw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import type { FilterEntry } from "@/lib/api-types";
import type { TimeBounds } from "@/lib/dashboard-queries";

/** ISO string the backend can compare against a DuckDB TIMESTAMP literal.
 *
 * Built from *local* wall-clock components on purpose. The bounds come back as
 * naive ISO (`datetime.isoformat()`, no offset), which `Date.parse` interprets
 * as local time – and the stored `timestamp` column is itself naive wall-clock.
 * Using `toISOString()` here would re-encode in UTC, shifting the literal by the
 * viewer's offset so the gte/lte window no longer lines up with the data (in a
 * +2h zone the filter matched everything → "the slider does nothing").
 */
function isoNoTz(ms: number): string {
  const d = new Date(ms);
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
    `T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  );
}

function fmt(ms: number): string {
  return new Date(ms).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * The dashboard's bottom time-range slider. Narrows the log's start/end window.
 * Dragging is visual only (the labels track the thumbs live); the range is
 * *committed on Apply*, at which point the two synthetic `timestamp` gte/lte
 * filter entries flow up to the `DashboardFilterProvider` and every widget
 * re-fetches (showing skeletons) against the narrowed window. Apply is explicit
 * so a multi-widget recompute only runs when the user has settled on a window.
 */
export function DashboardTimeRange({
  bounds,
  onChange,
}: {
  bounds: TimeBounds | undefined;
  onChange: (entries: FilterEntry[]) => void;
}) {
  const range = useMemo(() => {
    if (!bounds?.field || !bounds.min_ts || !bounds.max_ts) return null;
    const min = Date.parse(bounds.min_ts);
    const max = Date.parse(bounds.max_ts);
    if (Number.isNaN(min) || Number.isNaN(max) || max <= min) return null;
    return { field: bounds.field, min, max };
  }, [bounds]);

  // `value` is the live (possibly uncommitted) thumb position; `applied` is the
  // window currently driving the widgets. They diverge while the user drags.
  const [value, setValue] = useState<[number, number] | null>(null);
  const [applied, setApplied] = useState<[number, number] | null>(null);

  // Reset both to the full span whenever the bound log changes.
  useEffect(() => {
    const full = range ? ([range.min, range.max] as [number, number]) : null;
    setValue(full);
    setApplied(full);
  }, [range]);

  if (!range || !value || !applied) {
    return (
      <div className="flex items-center gap-2 border-t border-border bg-card/40 px-3 py-2 text-xs text-muted-foreground">
        <CalendarRange className="h-3.5 w-3.5" />
        No timestamp range available for this log.
      </div>
    );
  }

  const [start, end] = value;
  const narrowed = applied[0] > range.min || applied[1] < range.max;
  const dirty = value[0] !== applied[0] || value[1] !== applied[1];

  const apply = () => {
    const entries: FilterEntry[] = [];
    if (value[0] > range.min) {
      entries.push({ field: range.field, op: "gte", value: isoNoTz(value[0]) });
    }
    if (value[1] < range.max) {
      entries.push({ field: range.field, op: "lte", value: isoNoTz(value[1]) });
    }
    setApplied([value[0], value[1]]);
    onChange(entries);
  };

  const reset = () => {
    const full: [number, number] = [range.min, range.max];
    setValue(full);
    setApplied(full);
    onChange([]);
  };

  // One step per minute keeps the slider responsive on multi-year logs.
  const step = Math.max(60_000, Math.round((range.max - range.min) / 1000));

  return (
    <div className="flex items-center gap-3 border-t border-border bg-card/40 px-3 py-2.5">
      <CalendarRange className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
      <span className="shrink-0 tabular-nums text-xs text-muted-foreground">{fmt(start)}</span>
      <Slider
        min={range.min}
        max={range.max}
        step={step}
        value={value}
        onValueChange={(v) => setValue([v[0], v[1]] as [number, number])}
        className="flex-1"
      />
      <span className="shrink-0 tabular-nums text-xs text-muted-foreground">{fmt(end)}</span>
      <Button
        type="button"
        size="sm"
        className="h-7 shrink-0 px-2 text-xs"
        aria-label="Apply time range"
        disabled={!dirty}
        onClick={apply}
      >
        <Check className="mr-1 h-3.5 w-3.5" />
        Apply
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="h-7 w-7 shrink-0"
        aria-label="Reset time range"
        disabled={!narrowed && !dirty}
        onClick={reset}
      >
        <RotateCcw className="h-3.5 w-3.5" />
      </Button>
    </div>
  );
}
