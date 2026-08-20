"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { dashboardKeys, dashboardsListPath, dashboardPath } from "@/lib/query-keys";
import type { ColumnSpec, EventsPage, FilterEntry, LogModel } from "@/lib/api-types";

export type { LogModel };
// Re-exported from the pure `lib/query-keys.ts` (shared with the SSR prefetch layer).
export { dashboardKeys };

/**
 * Dashboards data layer.
 *
 * A dashboard is a grid of cards (each a module's `(module_id, widget_id)`)
 * bound to one event log. The card catalog (`useCardCatalog`) is aggregated by
 * the backend from every installed module's `frontend.widgets`; the palette
 * renders it and `useWidget(module_id, widget_id)` (lib/module-widgets) lazily
 * loads the actual bundle when a card mounts.
 *
 * Types mirror `apps/api/.../schemas/dashboards.py` + the `DashboardCard`
 * model in `routes/modules.py`.
 */

export interface DashboardItem {
  i: string;
  module_id: string;
  widget_id: string;
  title?: string | null;
  x: number;
  y: number;
  w: number;
  h: number;
  config: Record<string, unknown>;
}

/** How finely cards snap and how much air sits between them. No level
 * auto-compacts – cards always stay exactly where you place them; granularity
 * only changes the snap resolution. */
export type Granularity = "free" | "fine" | "medium" | "low";

/** Board-wide card appearance toggles, applied to every placed card. */
export interface CardChrome {
  border: boolean;
}

/** A named, saved set of global column filters – a reusable "saved filter". One
 * can be marked active so it applies on load in view mode. */
export interface FilterPreset {
  id: string;
  name: string;
  filters: FilterEntry[];
}

export interface CanvasSettings {
  granularity: Granularity;
  chrome: CardChrome;
  presets: FilterPreset[];
  /** Which preset applies on load (view mode). `null` = no saved filter. */
  active_preset_id: string | null;
}

export const DEFAULT_CARD_CHROME: CardChrome = { border: true };

export const DEFAULT_CANVAS_SETTINGS: CanvasSettings = {
  granularity: "medium",
  chrome: DEFAULT_CARD_CHROME,
  presets: [],
  active_preset_id: null,
};

/** The grid geometry each granularity maps to on the react-grid-layout canvas.
 * Granularity drives the *snap* resolution: `cols` (horizontal step) and
 * `rowHeight` (vertical step). Finer = more columns + shorter rows, so cards
 * snap in smaller increments. The dot-grid background stays a fixed texture and
 * is intentionally NOT tied to these. */
export interface GranularitySpec {
  label: string;
  description: string;
  cols: number;
  rowHeight: number;
  margin: [number, number];
  compactType: "vertical" | null;
}

export const GRANULARITY: Record<Granularity, GranularitySpec> = {
  free: {
    // The finest level: a very dense grid + no auto-arrange, so cards drag
    // essentially freely. (react-grid-layout is always column-based, so this is
    // the closest practical thing to "no snap".)
    label: "Free",
    description: "No snap – place freely",
    cols: 60,
    rowHeight: 8,
    margin: [2, 2],
    compactType: null,
  },
  fine: {
    label: "Fine",
    description: "Very fine snap",
    cols: 40,
    rowHeight: 12,
    margin: [4, 4],
    compactType: null,
  },
  medium: {
    label: "Medium",
    description: "Fine snap",
    cols: 24,
    rowHeight: 18,
    margin: [6, 6],
    compactType: null,
  },
  low: {
    label: "Low",
    description: "Coarse snap",
    cols: 12,
    rowHeight: 28,
    margin: [8, 8],
    compactType: null,
  },
};

/** When the column count changes (the user picks a different granularity),
 * rescale each card's `x`/`w` so it keeps the same relative position and width
 * instead of jumping. `h`/`y` are row-based and unbounded, so they're left as-is
 * – only the on-screen row height changes. */
export function rescaleColumns(
  items: DashboardItem[],
  fromCols: number,
  toCols: number,
): DashboardItem[] {
  if (fromCols === toCols || fromCols <= 0) return items;
  const f = toCols / fromCols;
  return items.map((it) => {
    const w = Math.max(1, Math.min(toCols, Math.round(it.w * f)));
    const x = Math.max(0, Math.min(toCols - w, Math.round(it.x * f)));
    return { ...it, x, w };
  });
}

/** Coerce an arbitrary stored value into valid canvas settings – older boards
 * predate the chrome/preset fields, so each is defaulted independently. */
export function canvasSettings(raw: Partial<CanvasSettings> | null | undefined): CanvasSettings {
  const g = raw?.granularity;
  const chrome: Partial<CardChrome> = raw?.chrome ?? {};
  const presets = Array.isArray(raw?.presets)
    ? raw.presets.filter(
        (p): p is FilterPreset =>
          !!p && typeof p.id === "string" && typeof p.name === "string" && Array.isArray(p.filters),
      )
    : [];
  const activeId = raw?.active_preset_id ?? null;
  return {
    granularity: g && g in GRANULARITY ? g : DEFAULT_CANVAS_SETTINGS.granularity,
    chrome: { border: chrome.border ?? DEFAULT_CARD_CHROME.border },
    presets,
    // Drop a dangling reference to a deleted preset.
    active_preset_id: presets.some((p) => p.id === activeId) ? activeId : null,
  };
}

/** The column filters the board should load with – its active preset, if any. */
export function activePresetFilters(settings: CanvasSettings): FilterEntry[] {
  const active = settings.presets.find((p) => p.id === settings.active_preset_id);
  return active ? active.filters : [];
}

export interface DashboardSummary {
  id: string;
  name: string;
  description: string | null;
  event_log_id: string | null;
  log_model: LogModel;
  card_count: number;
  updated_at: string;
}

export interface DashboardDetail {
  id: string;
  name: string;
  description: string | null;
  event_log_id: string | null;
  log_model: LogModel;
  items: DashboardItem[];
  settings: CanvasSettings;
  created_at: string;
  updated_at: string;
  /** False when the board was opened via a share – render it read-only. */
  is_owner: boolean;
}

/** One configurable field on a card, in the module `config_schema` dialect. */
export interface WidgetPropSchema {
  type?: "number" | "integer" | "string" | "boolean";
  title?: string;
  description?: string;
  default?: unknown;
  minimum?: number;
  maximum?: number;
  step?: number;
  enum?: string[];
  /** Optional display labels parallel to `enum` (falls back to the value). */
  enumLabels?: string[];
  ui?: { widget?: string };
}

export interface WidgetConfigSchema {
  properties?: Record<string, WidgetPropSchema>;
}

export interface DashboardCard {
  module_id: string;
  module_name: string;
  widget_id: string;
  title: string;
  description: string | null;
  icon: string | null;
  default_w: number;
  default_h: number;
  /** Whether the card can be resized. When false it's a fixed size locked to
   * `default_w`/`default_h`; when true it resizes no smaller than `min_w`/`min_h`. */
  resizable: boolean;
  /** Smallest size a resizable card may be shrunk to (RGL cells); the canvas
   * applies these as the grid item's `minW`/`minH`. Ignored when not resizable. */
  min_w: number;
  min_h: number;
  config_schema: WidgetConfigSchema | null;
  /** Log data model(s) this card applies to. The palette only shows a card
   * whose models include the board's model. */
  log_models: LogModel[];
}

/** Seed a placement's `config` from its schema defaults when a card is added. */
export function configDefaults(schema: WidgetConfigSchema | null | undefined): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, prop] of Object.entries(schema?.properties ?? {})) {
    if (prop.default !== undefined) out[key] = prop.default;
  }
  return out;
}

export interface DashboardExport {
  kind: string;
  version: number;
  name: string;
  description: string | null;
  log_model: LogModel;
  items: DashboardItem[];
  settings: CanvasSettings;
}

export function useDashboards() {
  return useQuery({
    queryKey: dashboardKeys.all(),
    queryFn: () => api<DashboardSummary[]>(dashboardsListPath()),
  });
}

export function useDashboard(id: string | null) {
  return useQuery({
    queryKey: id ? dashboardKeys.detail(id) : ["dashboards", "noop"],
    queryFn: () => api<DashboardDetail>(dashboardPath(id ?? "")),
    enabled: !!id,
  });
}

/** Every card exposed by the modules the user owns – powers the palette. */
export function useCardCatalog() {
  return useQuery({
    queryKey: dashboardKeys.cards(),
    queryFn: () => api<DashboardCard[]>("/api/v1/modules/cards"),
    staleTime: 60_000,
  });
}

export function useCreateDashboard() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { name: string; description?: string | null; log_model: LogModel }) =>
      api<DashboardDetail>("/api/v1/dashboards", { method: "POST", json: input }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: dashboardKeys.all() });
    },
  });
}

export interface DashboardPatch {
  name?: string;
  description?: string | null;
  event_log_id?: string | null;
  items?: DashboardItem[];
  settings?: CanvasSettings;
}

export function useUpdateDashboard(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: DashboardPatch) =>
      api<DashboardDetail>(`/api/v1/dashboards/${id}`, { method: "PATCH", json: patch }),
    onSuccess: (data) => {
      qc.setQueryData(dashboardKeys.detail(id), data);
      void qc.invalidateQueries({ queryKey: dashboardKeys.all() });
    },
  });
}

export function useDeleteDashboard() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api<void>(`/api/v1/dashboards/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: dashboardKeys.all() });
    },
  });
}

/** Earliest/latest timestamp in a log – seeds the time-range slider. */
export interface TimeBounds {
  field: string | null;
  min_ts: string | null;
  max_ts: string | null;
}

export function useTimeBounds(logId: string | null) {
  return useQuery({
    queryKey: ["event-log-time-bounds", logId],
    queryFn: () => api<TimeBounds>(`/api/v1/event-logs/${logId}/time-bounds`),
    enabled: !!logId,
    staleTime: 5 * 60_000,
  });
}

/** Column specs for a log – backs the dashboard's global filter bar. Reuses
 * the events endpoint (one row) since it returns the inferred `columns`. */
export function useEventColumns(logId: string | null) {
  return useQuery({
    queryKey: ["event-log-columns", logId],
    queryFn: async () => {
      const page = await api<EventsPage>(`/api/v1/event-logs/${logId}/events?limit=1`);
      return page.columns.filter((c) => !c.name.startsWith("_"));
    },
    enabled: !!logId,
    staleTime: 5 * 60_000,
  });
}

export type { ColumnSpec };

export function useImportDashboard() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (doc: {
      name?: string;
      description?: string | null;
      log_model?: LogModel;
      items: DashboardItem[];
      settings?: CanvasSettings;
    }) => api<DashboardDetail>("/api/v1/dashboards/import", { method: "POST", json: doc }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: dashboardKeys.all() });
    },
  });
}
