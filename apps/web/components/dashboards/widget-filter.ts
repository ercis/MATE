import type { FilterEntry } from "@/lib/api-types";

/**
 * Per-widget (per-card) filtering — shared model + the single precedence rule.
 *
 * A dashboard card may carry its OWN optional filter (column filters + a
 * time-range window), stored inside the card's permissive `config` JSON blob so
 * no API/schema change is needed. It travels to the module route on the exact
 * same `X-FF-Event-Filter` header the board-global filter uses (identical wire
 * format), and the backend decodes both the same way.
 *
 * GLOBAL ALWAYS WINS: when a board-global filter is active it already rides the
 * ambient header (`DashboardFilterProvider`), and a card's own filter is
 * ignored. `effectiveWidgetFilterHeader` is the one place that rule lives.
 */

/** The request header the dashboard's ephemeral filter travels on. Shared with
 * the global filter provider so both serialize identically. */
export const EVENT_FILTER_HEADER = "X-FF-Event-Filter";

/** Reserved key the card's per-widget filter lives under inside `item.config`.
 * Namespaced so it can never collide with a module widget's or a viz's own
 * option keys (and is harmless if a widget/viz receives it in its config). */
export const WIDGET_FILTER_CONFIG_KEY = "__ff_widget_filter__";

/** A card's optional per-widget filter — the same two `FilterEntry[]` channels
 * the board-global bar uses: column filters + a time-range window. */
export interface WidgetFilter {
  columnFilters: FilterEntry[];
  timeFilters: FilterEntry[];
}

export const EMPTY_WIDGET_FILTER: WidgetFilter = { columnFilters: [], timeFilters: [] };

function isFilterEntryArray(v: unknown): v is FilterEntry[] {
  return (
    Array.isArray(v) &&
    v.every((e) => !!e && typeof e === "object" && "field" in e && "op" in e)
  );
}

/** Read a card's stored per-widget filter out of its `config` blob, tolerating
 * absent/legacy values (⇒ empty filter). */
export function readWidgetFilter(config: Record<string, unknown> | undefined | null): WidgetFilter {
  const raw = config?.[WIDGET_FILTER_CONFIG_KEY];
  if (!raw || typeof raw !== "object") return EMPTY_WIDGET_FILTER;
  const r = raw as Record<string, unknown>;
  return {
    columnFilters: isFilterEntryArray(r.columnFilters) ? r.columnFilters : [],
    timeFilters: isFilterEntryArray(r.timeFilters) ? r.timeFilters : [],
  };
}

/** True when a widget filter carries at least one entry. */
export function hasWidgetFilter(f: WidgetFilter): boolean {
  return f.columnFilters.length > 0 || f.timeFilters.length > 0;
}

/** Write a card's per-widget filter back into a new `config` blob. An empty
 * filter drops the reserved key entirely, so a cleared card round-trips clean. */
export function writeWidgetFilter(
  config: Record<string, unknown> | undefined | null,
  filter: WidgetFilter,
): Record<string, unknown> {
  const next = { ...(config ?? {}) };
  if (hasWidgetFilter(filter)) {
    next[WIDGET_FILTER_CONFIG_KEY] = {
      columnFilters: filter.columnFilters,
      timeFilters: filter.timeFilters,
    };
  } else {
    delete next[WIDGET_FILTER_CONFIG_KEY];
  }
  return next;
}

/** A `config` blob with the reserved widget-filter key stripped — so the value
 * passed on to a viz/widget as its options never leaks the internal key. */
export function configWithoutWidgetFilter(
  config: Record<string, unknown> | undefined | null,
): Record<string, unknown> {
  if (!config || !(WIDGET_FILTER_CONFIG_KEY in config)) return config ?? {};
  const { [WIDGET_FILTER_CONFIG_KEY]: _omit, ...rest } = config;
  return rest;
}

/** UTF-8-safe base64 of the filter payload — identical wire format to the global
 * bar's header so the backend decodes both the same way. `btoa` alone breaks on
 * non-Latin1 filter values. */
export function encodeFilterHeader(entries: FilterEntry[]): string {
  const json = JSON.stringify({ filter: entries });
  const bytes = new TextEncoder().encode(json);
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary);
}

/**
 * The single, centralized precedence rule for a widget's data request.
 *
 * GLOBAL ALWAYS WINS: when the board-global filter is active it already rides
 * the ambient `X-FF-Event-Filter` header, so this returns `null` and lets that
 * header apply — deliberately NOT emitting a per-request header. (The ambient
 * merge in `lib/api.ts` never overrides a header the caller set, so emitting one
 * would *beat* the global filter — the opposite of the required precedence.)
 * Only when no global filter is active does the widget's own filter take effect.
 *
 * Returns the header value to attach on this widget's request, or `null` to
 * attach nothing (defer to the ambient/global header, or send unfiltered).
 */
export function effectiveWidgetFilterHeader(
  globalActive: boolean,
  widget: WidgetFilter,
): string | null {
  if (globalActive) return null;
  // Mirror the global provider's order: time entries first, then columns.
  const entries = [...widget.timeFilters, ...widget.columnFilters];
  return entries.length > 0 ? encodeFilterHeader(entries) : null;
}
