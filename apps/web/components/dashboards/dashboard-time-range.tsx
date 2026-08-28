"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { CalendarRange, Check, RotateCcw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { cn } from "@/lib/cn";
import type { FilterEntry } from "@/lib/api-types";
import { toNaiveUtcString } from "@/lib/format";
import type { TimeBounds } from "@/lib/dashboard-queries";

// The bounds arrive as UTC ISO *with* offset, so `Date.parse` yields the true
// instant. The stored `timestamp` column is naive UTC wall-clock, so the
// gte/lte literals sent back must be the instant's UTC components without an
// offset (`toNaiveUtcString`) – local components (or `toISOString`'s suffix)
// would shift the window by the viewer's offset and the slider stops lining
// up with the data.

function fmt(ms: number): string {
  return new Date(ms).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Reconstruct the slider window from the board's committed time entries so a
 * persisted / shared time range shows on the thumbs (not just in the data). The
 * entries carry naive-UTC wall-clock literals (`toNaiveUtcString`), so parse
 * them as UTC (`+ "Z"`) to recover the instant. Missing gte/lte ⇒ the full
 * bound; the window is clamped to the range and falls back to full on garbage. */
function windowFromCommitted(
  committed: FilterEntry[] | undefined,
  range: { field: string; min: number; max: number },
): [number, number] {
  let start = range.min;
  let end = range.max;
  for (const f of committed ?? []) {
    if (f.field !== range.field || typeof f.value !== "string") continue;
    const ms = Date.parse(`${f.value}Z`);
    if (Number.isNaN(ms)) continue;
    const clamped = Math.min(Math.max(ms, range.min), range.max);
    if (f.op === "gte") start = clamped;
    else if (f.op === "lte") end = clamped;
  }
  return end < start ? [range.min, range.max] : [start, end];
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
  committed,
  compact = false,
  onChange,
}: {
  bounds: TimeBounds | undefined;
  /** The board's committed time-range entries – seeds the thumbs on mount so a
   * persisted (and thus *shared*) window shows, instead of always full span. */
  committed?: FilterEntry[];
  /** Thin-strip variant for the module-panel shell (`LogFilterProvider`). Same
   * controls, less padding. Dashboards leave it off. */
  compact?: boolean;
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

  // Seed from the committed window on the *first* run only (read via ref so a
  // later apply/reset – which changes `committed` – doesn't stomp the thumbs);
  // a subsequent bound-log change resets to the full span (the old window is
  // meaningless for the new log).
  const committedRef = useRef(committed);
  committedRef.current = committed;
  const seededRef = useRef(false);
  useEffect(() => {
    if (!range) {
      setValue(null);
      setApplied(null);
      return;
    }
    const w = seededRef.current
      ? ([range.min, range.max] as [number, number])
      : windowFromCommitted(committedRef.current, range);
    seededRef.current = true;
    setValue(w);
    setApplied(w);
  }, [range]);

  if (!range || !value || !applied) {
    return (
      <div
        className={cn(
          "flex items-center gap-2 border-t border-white/10 bg-card/40 backdrop-blur-md text-xs text-muted-foreground",
          compact ? "px-2 py-1" : "px-3 py-2",
        )}
      >
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
      entries.push({ field: range.field, op: "gte", value: toNaiveUtcString(value[0]) });
    }
    if (value[1] < range.max) {
      entries.push({ field: range.field, op: "lte", value: toNaiveUtcString(value[1]) });
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
    <div
      className={cn(
        "flex items-center border-t border-white/10 bg-card/40 backdrop-blur-md",
        compact ? "gap-2 px-2 py-1" : "gap-3 px-3 py-2.5",
      )}
    >
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
        className={cn("shrink-0 px-2 text-xs", compact ? "h-6" : "h-7")}
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
        className={cn("shrink-0", compact ? "h-6 w-6" : "h-7 w-7")}
        aria-label="Reset time range"
        disabled={!narrowed && !dirty}
        onClick={reset}
      >
        <RotateCcw className="h-3.5 w-3.5" />
      </Button>
    </div>
  );
}
