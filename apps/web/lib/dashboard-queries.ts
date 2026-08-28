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

/** Points a `kind:"viz"` card at a module dataset (manifest `datasets:`). */
export interface DatasetRef {
  module_id: string;
  dataset_id: string;
}

export interface DashboardItem {
  i: string;
  /** Discriminates the card type. Absent on legacy items (they predate the
   * field) ⇒ treated as "widget". */
  kind?: "widget" | "viz";
  /** widget cards: present when kind is "widget" (or legacy). */
  module_id?: string;
  widget_id?: string;
  /** viz cards: the dataset + chosen generic viz + field mapping. */
  dataset_ref?: DatasetRef | null;
  viz_id?: string | null;
  mapping?: Record<string, unknown>;
  title?: string | null;
  x: number;
  y: number;
  w: number;
  h: number;
  config: Record<string, unknown>;
}

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
  /** Bumped when stored geometry changes meaning. v2 = the fixed 12-column
   * grid. The server strips the pre-v2 `granularity` key, so a board is
   * migrated exactly once (see `schemas/dashboards.py`). */
  grid_version?: number;
  chrome: CardChrome;
  presets: FilterPreset[];
  /** Which preset applies on load (view mode). `null` = no saved filter. */
  active_preset_id: string | null;
  /** The board's committed live column-filter bar. Persisted so a *shared*
   * board opens on the owner's filtered view. `undefined` (legacy / never
   * committed) ⇒ fall back to the active preset. */
  column_filters?: FilterEntry[];
  /** The board's committed time-range window (0–2 synthetic `timestamp`
   * gte/lte entries). Persisted so a shared board opens on the owner's time
   * range. `undefined` ⇒ full span. */
  time_filters?: FilterEntry[];
}

export const DEFAULT_CARD_CHROME: CardChrome = { border: true };

export const GRID_VERSION = 2;

export const DEFAULT_CANVAS_SETTINGS: CanvasSettings = {
  grid_version: GRID_VERSION,
  chrome: DEFAULT_CARD_CHROME,
  presets: [],
  active_preset_id: null,
};

/**
 * The board grid. One fixed 12-column layout — there is no per-board snap
 * level any more.
 *
 * The four old granularities (12/24/40/60 columns with 28/18/12/8px rows) made
 * a grid cell mean something different on every board, which in turn made a
 * widget's declared `min_w`/`min_h` meaningless: the same manifest minimum
 * rendered 3.6× larger on a "low" board than a "free" one. Cards now size
 * against one grid plus absolute pixel floors from the manifest
 * (`min_px_w`/`min_px_h`), so a minimum is a real size.
 *
 * `rowHeight` is deliberately the old "medium" value: stored `y`/`h` keep
 * their meaning, so the migration only had to rescale `x`/`w`.
 *
 * No auto-compaction — cards stay exactly where you place them. The dot-grid
 * background is a fixed texture and is intentionally NOT tied to these.
 */
export const GRID = {
  cols: 12,
  rowHeight: 18,
  margin: [8, 8] as [number, number],
} as const;

/** Below this measured width the board abandons the grid and stacks cards in
 * one column (and edit mode is suppressed) — 12 columns of ~50px are unusable
 * for any real card. */
export const STACK_BELOW_PX = 720;

/**
 * Convert an absolute pixel floor into a row count on the grid.
 *
 * This is the half of the min-size fix that a column count can't express: rows
 * are a fixed height, so `min_px_h` maps to rows independently of how wide the
 * board is. (The horizontal floor, `min_px_w`, has to be resolved against the
 * measured column width instead — see `constraintsFor` in dashboard-canvas.)
 */
export function rowsForPx(px: number): number {
  if (!Number.isFinite(px) || px <= 0) return 0;
  return Math.ceil((px + GRID.margin[1]) / (GRID.rowHeight + GRID.margin[1]));
}

/** Coerce an arbitrary stored value into valid canvas settings – older boards
 * predate the chrome/preset fields, so each is defaulted independently. */
export function canvasSettings(raw: Partial<CanvasSettings> | null | undefined): CanvasSettings {
  const chrome: Partial<CardChrome> = raw?.chrome ?? {};
  const presets = Array.isArray(raw?.presets)
    ? raw.presets.filter(
        (p): p is FilterPreset =>
          !!p && typeof p.id === "string" && typeof p.name === "string" && Array.isArray(p.filters),
      )
    : [];
  const activeId = raw?.active_preset_id ?? null;
  return {
    // The server has already rescaled any pre-v2 geometry and stripped the
    // legacy `granularity` key, so the client never has to interpret it.
    grid_version: raw?.grid_version ?? GRID_VERSION,
    chrome: { border: chrome.border ?? DEFAULT_CARD_CHROME.border },
    presets,
    // Drop a dangling reference to a deleted preset.
    active_preset_id: presets.some((p) => p.id === activeId) ? activeId : null,
    // Kept as-is (arrays) or dropped to `undefined` (absent ⇒ legacy board).
    column_filters: Array.isArray(raw?.column_filters)
      ? (raw.column_filters as FilterEntry[])
      : undefined,
    time_filters: Array.isArray(raw?.time_filters)
      ? (raw.time_filters as FilterEntry[])
      : undefined,
  };
}

/** The column filters the board should load with – its active preset, if any. */
export function activePresetFilters(settings: CanvasSettings): FilterEntry[] {
  const active = settings.presets.find((p) => p.id === settings.active_preset_id);
  return active ? active.filters : [];
}

/** The column filters the board should *load* with. Prefers the committed live
 * bar (so a shared board opens on the owner's exact view); falls back to the
 * active preset for legacy boards that never committed a live bar. */
export function initialColumnFilters(settings: CanvasSettings): FilterEntry[] {
  return settings.column_filters ?? activePresetFilters(settings);
}

/** The time-range window the board should load with (empty ⇒ full span). */
export function initialTimeFilters(settings: CanvasSettings): FilterEntry[] {
  return settings.time_filters ?? [];
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

/** Plain-language help for a widget or a module panel, shown behind its ⓘ.
 * Three questions a reader actually asks, rather than one blob. */
export interface WidgetHelp {
  what: string;
  read?: string | null;
  computed?: string | null;
  docs_url?: string | null;
}

/** One of the module's views a card can render. `exposes` names the
 * `config_schema` keys meaningful for that view, so the card's settings show
 * only the knobs that actually apply to it. */
export interface WidgetView {
  id: string;
  title: string;
  description?: string | null;
  exposes?: string[];
}

/** One figure a multi-KPI card can show. */
export interface WidgetKpi {
  id: string;
  title: string;
  /** Per-KPI ⓘ text, distinct from the card-level `help`. */
  info?: string | null;
  /** Whether this KPI is on when the card is first placed. */
  default?: boolean;
}

/** Where clicking into a card navigates. Absent, the platform still targets
 * the declaring module with no params. */
export interface WidgetDrill {
  module_id?: string | null;
  /** Static params merged into every drill; a param passed at click time wins. */
  params?: Record<string, string>;
  label?: string | null;
  enabled?: boolean;
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
  /** Smallest size a resizable card may be shrunk to, in grid units on the
   * fixed 12-column grid. Ignored when not resizable. */
  min_w: number;
  min_h: number;
  /** Absolute pixel floors, independent of the grid. These are what actually
   * make a minimum meaningful: a grid unit is only a real size once you know
   * the container width, so the canvas resolves these against the measured
   * width and takes whichever floor is larger. Optional — a widget that
   * declares neither falls back to `min_w`/`min_h` alone. */
  min_px_w?: number;
  min_px_h?: number;
  config_schema: WidgetConfigSchema | null;
  /** Structured help behind the card's ⓘ. `description` stays the one-line
   * palette blurb; this is the real explanation. */
  help?: WidgetHelp | null;
  /** Module views this card can render, and which config keys apply to each.
   * Empty ⇒ a single implicit view. */
  views?: WidgetView[];
  /** The figures a multi-KPI card shows, so a placement can render a subset.
   * The chosen ids live in the placement's `config.kpis`. */
  kpis?: WidgetKpi[];
  /** Where "open in module" and in-card clicks navigate. */
  drill?: WidgetDrill | null;
  /** Whether the widget ships its own settings component. The URL is
   * conventional: `/api/v1/modules/{id}/assets/widget-{widget_id}-settings.js`. */
  has_settings_entry?: boolean;
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

/** One module *dataset* (manifest `datasets:`) the palette can drop as a
 * generic-viz card. Mirrors `DatasetCatalogEntry` in `routes/datasets.py`. */
export interface DatasetCatalogEntry {
  module_id: string;
  module_name: string;
  dataset_id: string;
  title: string;
  description: string | null;
  icon: string | null;
  /** The data shape – drives which generic viz can render it. */
  shape: "table" | "graph" | "kpi" | "tree" | "blob";
  /** Module sub-route (leading slash) the card fetches the data from. */
  route: string;
  log_models: LogModel[];
  params_schema: WidgetConfigSchema | null;
}

/** Every dataset exposed by the modules the user owns – powers the palette's
 * generic-viz section. Filtered by the board's `log_model` client-side, exactly
 * like `useCardCatalog`. */
export function useDatasetCatalog() {
  return useQuery({
    queryKey: ["datasets", "catalog"],
    queryFn: () => api<DatasetCatalogEntry[]>("/api/v1/datasets/catalog"),
    staleTime: 60_000,
  });
}

export function useCreateDashboard() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      name: string;
      description?: string | null;
      log_model: LogModel;
      /** When set, the server seeds the board from a curated starter template
       * (its items/settings/model win); the model above is then ignored. */
      template_id?: string;
    }) => api<DashboardDetail>("/api/v1/dashboards", { method: "POST", json: input }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: dashboardKeys.all() });
    },
  });
}

/** A curated starter board offered by the "start from template" picker. Mirrors
 * `DashboardTemplate` in `apps/api/.../schemas/dashboards.py`. Typed locally
 * until `make codegen` regenerates `lib/api-types.ts` (API must be on :8000). */
export interface DashboardTemplate {
  id: string;
  name: string;
  description: string;
  log_model: LogModel;
  card_count: number;
}

/** The curated starter boards. Static + global, so cache them generously. */
export function useDashboardTemplates() {
  return useQuery({
    queryKey: ["dashboard-templates"],
    queryFn: () => api<DashboardTemplate[]>("/api/v1/dashboards/templates"),
    staleTime: 5 * 60_000,
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
    onMutate: async (patch) => {
      await qc.cancelQueries({ queryKey: dashboardKeys.detail(id) });
      const prev = qc.getQueryData<DashboardDetail>(dashboardKeys.detail(id));
      if (prev) {
        qc.setQueryData<DashboardDetail>(dashboardKeys.detail(id), { ...prev, ...patch });
      }
      return { prev };
    },
    onError: (_e, _patch, ctx) => {
      if (ctx?.prev) qc.setQueryData(dashboardKeys.detail(id), ctx.prev);
    },
    onSuccess: (data) => {
      // The PATCH returns the authoritative row – write it through so the
      // detail cache reconciles without waiting for the refetch.
      qc.setQueryData(dashboardKeys.detail(id), data);
    },
    onSettled: () => {
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
