"use client";

import { Filter } from "lucide-react";

import { DashboardFilterBar } from "@/components/dashboards/dashboard-filter-bar";
import { useEventColumns } from "@/lib/dashboard-queries";
import type { WidgetFilter } from "@/components/dashboards/widget-filter";

/**
 * A card's optional per-widget filter, shown in its settings popover. Reuses the
 * board-global `DashboardFilterBar` (the same `ColumnFilter` building block) so
 * the add/edit/remove UX is identical — including time-range filtering, offered
 * through the timestamp column's `≥`/`≤` operators (the compact bar fits the
 * narrow settings popover where the full-width board time slider would not).
 *
 * The value is stored on the card's `config` (`writeWidgetFilter`) and applied
 * to the card's OWN data request — unless a board-global filter is active, which
 * always takes precedence (`effectiveWidgetFilterHeader`).
 */
export function WidgetFilterEditor({
  logId,
  value,
  onChange,
}: {
  logId: string | null;
  value: WidgetFilter;
  onChange: (next: WidgetFilter) => void;
}) {
  const { data: columns } = useEventColumns(logId);

  return (
    <div className="space-y-1.5 border-t border-border/60 pt-3">
      <div className="flex items-center gap-1.5">
        <Filter className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="text-xs font-medium">Widget filter</span>
      </div>
      <p className="text-[11px] leading-snug text-muted-foreground">
        Applies to this card only. The board filter overrides it when active.
      </p>
      {!logId ? (
        <p className="text-[11px] text-muted-foreground">Select an event log to filter this card.</p>
      ) : !columns || columns.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">No filterable columns for this log.</p>
      ) : (
        <div className="overflow-hidden rounded-md border border-border/60">
          <DashboardFilterBar
            logId={logId}
            columns={columns}
            filters={value.columnFilters}
            onChange={(columnFilters) => onChange({ ...value, columnFilters })}
          />
        </div>
      )}
    </div>
  );
}
