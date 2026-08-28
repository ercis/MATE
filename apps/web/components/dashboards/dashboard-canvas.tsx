"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useRouter } from "next/navigation";
import { useReducedMotion } from "framer-motion";
import RGL, { WidthProvider, type Layout } from "react-grid-layout";

import { cn } from "@/lib/cn";
import {
  drillLabel,
  resolveDrillHref,
  type DrillHandler,
} from "@/lib/dashboards/drill";
import { DashboardCard } from "@/components/dashboards/dashboard-card";
import {
  configDefaults,
  GRID,
  rowsForPx,
  STACK_BELOW_PX,
  useCardCatalog,
  useDatasetCatalog,
  type CanvasSettings,
  type DashboardCard as CatalogCard,
  type DashboardItem,
  type DatasetCatalogEntry,
  type WidgetConfigSchema,
} from "@/lib/dashboard-queries";
import type { CardPatch } from "@/components/dashboards/dashboard-card";
import { defaultVizForShape, vizRegistry } from "@/lib/visualizations/registry";

import "react-grid-layout/css/styles.css";

const GridLayout = WidthProvider(RGL);

/** Do two grid rects overlap? (excludes the item against itself). */
function collides(a: DashboardItem, b: DashboardItem): boolean {
  return (
    a.i !== b.i && a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y
  );
}

function firstCollision(list: DashboardItem[], it: DashboardItem): DashboardItem | null {
  for (const o of list) if (collides(o, it)) return o;
  return null;
}

/**
 * First-fit placement for a newly added card. Scans rows top-to-bottom and, within
 * each row, columns left-to-right, returning the highest cell where a `w×h` card
 * fits without overlapping an existing one. So a plain palette click fills a gap at
 * the top (e.g. the free space to the right of a half-full first row) instead of
 * always landing at the bottom, and only drops to a new row once the rows above are
 * full. Existing cards never move (no compaction).
 */
function findTopFit(items: DashboardItem[], w: number, h: number, cols: number): { x: number; y: number } {
  const width = Math.min(w, cols);
  const overlaps = (x: number, y: number) =>
    items.some((it) => x < it.x + it.w && x + width > it.x && y < it.y + it.h && y + h > it.y);
  for (let y = 0; ; y++) {
    for (let x = 0; x + width <= cols; x++) {
      if (!overlaps(x, y)) return { x, y };
    }
  }
}

/**
 * Non-compacting reflow. Places `id` at `(x, y)`, pushes *only* the cards it
 * (transitively) overlaps straight down, and leaves every other card exactly
 * where `snapshot` had it. It's recomputed from the drag-start snapshot on every
 * pointer move, so displaced cards spring back the instant the dragged card
 * leaves them – while intentional gaps survive (there's no vertical compaction,
 * unlike react-grid-layout's built-in `compactType: "vertical"`).
 */
function reflowFree(snapshot: DashboardItem[], id: string, x: number, y: number): DashboardItem[] {
  return reflowPinned(snapshot, new Map([[id, { x, y }]]));
}

/**
 * The general form: pin any number of cards at given positions and let the
 * rest yield around them.
 *
 * Multi-select drag needs a *set* of fixed obstacles, not one — the selected
 * cards move together and must not push each other. Everything else (single
 * drag, resize, keyboard nudge, the palette add-ghost) is the one-card case, so
 * they all share this implementation and can't drift apart.
 */
function reflowPinned(
  snapshot: DashboardItem[],
  pinned: Map<string, { x: number; y: number }>,
): DashboardItem[] {
  const result = snapshot.map((it) => ({ ...it }));
  // The pinned cards are the fixed obstacles everyone yields to.
  const placed: DashboardItem[] = [];
  for (const it of result) {
    const at = pinned.get(it.i);
    if (!at) continue;
    it.x = at.x;
    it.y = at.y;
    placed.push(it);
  }
  if (placed.length === 0) return result;
  // Resolve the rest in reading order, pushing each below whatever it lands on,
  // then add it to the obstacle set so later cards cascade off it too.
  const others = result
    .filter((it) => !pinned.has(it.i))
    .sort((a, b) => a.y - b.y || a.x - b.x);
  for (const it of others) {
    let guard = 0;
    let c: DashboardItem | null;
    while ((c = firstCollision(placed, it)) && guard++ < 1000) it.y = c.y + c.h;
    placed.push(it);
  }
  return result;
}

/** Move every card in `ids` by a grid delta, clamped to the grid, and reflow
 * the rest around them. Backs both multi-select drag and keyboard nudge. */
function reflowDelta(
  snapshot: DashboardItem[],
  ids: readonly string[],
  dx: number,
  dy: number,
  cols: number,
): DashboardItem[] {
  const pinned = new Map<string, { x: number; y: number }>();
  for (const id of ids) {
    const it = snapshot.find((c) => c.i === id);
    if (!it) continue;
    pinned.set(id, {
      x: Math.max(0, Math.min(cols - it.w, it.x + dx)),
      y: Math.max(0, it.y + dy),
    });
  }
  return reflowPinned(snapshot, pinned);
}

type FreeDrag = {
  id: string;
  pointerX: number;
  pointerY: number;
  startLeft: number;
  startTop: number;
  stepX: number;
  stepY: number;
  padX: number;
  padY: number;
  w: number;
  cols: number;
  snapshot: DashboardItem[];
};

type FreeResize = {
  id: string;
  pointerX: number;
  pointerY: number;
  stepX: number;
  stepY: number;
  x: number;
  y: number;
  startW: number;
  startH: number;
  minW: number;
  minH: number;
  cols: number;
  snapshot: DashboardItem[];
};

/** A palette item being added: a module-authored widget card, or a generic-viz
 * card bound to a module dataset. */
export type AddRequest =
  | { kind: "widget"; card: CatalogCard }
  | { kind: "viz"; dataset: DatasetCatalogEntry };

/** Synchronously begins a palette→canvas add at the pointer's position. The
 * canvas hands one of these to the palette (via a ref) so the palette's
 * `pointerdown` attaches the drag listeners in the same tick — identical to the
 * canvas's own free-drag, not deferred through React state + an effect. */
export type AddStarter = (req: AddRequest, e: React.PointerEvent) => void;

/** Drop size for an add request: the widget's declared size, or the default
 * viz's default geometry for a dataset (sized by its shape's default viz). */
function addGeometry(req: AddRequest): { w: number; h: number } {
  if (req.kind === "widget") return { w: req.card.default_w, h: req.card.default_h };
  const d = vizRegistry[defaultVizForShape(req.dataset.shape)]?.defaults;
  return { w: d?.w ?? 6, h: d?.h ?? 8 };
}

/** Build the placed item for an add request at `(x, y)`. A widget card carries
 * `module_id`/`widget_id`; a viz card carries `dataset_ref` + a default viz for
 * the dataset's shape (empty mapping ⇒ renders immediately or prompts config). */
function buildAddItem(req: AddRequest, i: string, x: number, y: number): DashboardItem {
  if (req.kind === "widget") {
    const c = req.card;
    return {
      i,
      kind: "widget",
      module_id: c.module_id,
      widget_id: c.widget_id,
      title: c.title,
      x,
      y,
      w: c.default_w,
      h: c.default_h,
      config: configDefaults(c.config_schema),
    };
  }
  const ds = req.dataset;
  const vizId = defaultVizForShape(ds.shape);
  const g = vizRegistry[vizId]?.defaults;
  return {
    i,
    kind: "viz",
    dataset_ref: { module_id: ds.module_id, dataset_id: ds.dataset_id },
    viz_id: vizId,
    mapping: {},
    title: ds.title,
    x,
    y,
    w: g?.w ?? 6,
    h: g?.h ?? 8,
    config: {},
  };
}

/** Layout/child id of the live placeholder shown while adding from the palette. */
const ADD_GHOST_ID = "__add_ghost__";

/** Stable empty selection — a fresh `[]` default would change identity every
 * render and invalidate every memo that depends on it. */
const EMPTY_SELECTION: readonly string[] = [];

/** Placement id for a newly added or duplicated card. */
function newCardId(): string {
  return typeof crypto !== "undefined" && crypto.randomUUID
    ? crypto.randomUUID()
    : `card-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/** RGL's own container height (`containerHeight()`): rows × rowHeight plus the
 * inter-row gaps plus top+bottom container padding (which defaults to margin).
 * Exported so the view can compute the fit-to-view zoom without measuring DOM. */
export function gridPixelHeight(
  items: readonly { y: number; h: number }[],
  g: { rowHeight: number; margin: [number, number] } = GRID,
): number {
  const bottom = items.reduce((m, it) => Math.max(m, it.y + it.h), 0);
  return bottom * g.rowHeight + (bottom + 1) * g.margin[1];
}

/** Pixel height of a card `h` rows tall (the inter-row gaps count too). Used by
 * stacked mode, which has no grid to lay cards out on. */
function rowsToPx(h: number): number {
  return h * GRID.rowHeight + (h - 1) * GRID.margin[1];
}

/** The measured content width of an element, tracked live.
 *
 * The canvas needs this because a widget's `min_px_w` floor can only be turned
 * into a column count once the column width is known — and that changes with
 * the viewport, the palette, and the inspector. */
function useContainerWidth(ref: React.RefObject<HTMLElement | null>): number {
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      if (entry) setWidth(entry.contentRect.width);
    });
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, [ref]);
  return width;
}

/**
 * The react-grid-layout canvas. In edit mode it accepts adds from the palette
 * (via `startAddRef`), and drag/resize via the card header handle. Geometry
 * changes flow back through `onItemsChange`; the parent owns the item list.
 *
 * One fixed 12-column grid (`GRID`) — there is no per-board snap level. Cards
 * never auto-compact, so they stay exactly where you place them, and a card's
 * minimum size comes from its manifest as both grid units and absolute pixel
 * floors (see `constraintsFor`).
 *
 * Drag, resize AND palette adds are all driven here rather than by RGL: RGL
 * won't let the layout prop move non-dragged cards mid-drag, and without
 * compaction it never springs pushed cards back. So a fully controlled layout
 * is recomputed per pointer move (see `reflowFree`), and RGL is left as a pure
 * positioning engine. (HTML5 drag-drop is unusable regardless — it never
 * reaches the canvas behind the prod proxy.)
 *
 * Below `STACK_BELOW_PX` the grid is abandoned for a read-only single column.
 */
export function DashboardCanvas({
  items,
  logId,
  editing,
  startAddRef,
  settings,
  zoom = 1,
  selectedIds = EMPTY_SELECTION,
  onSelectionChange,
  historyApplying,
  onItemsChange,
}: {
  items: DashboardItem[];
  logId: string | null;
  editing: boolean;
  /** The canvas publishes its add-drag starter here so the palette can call it
   * synchronously from `pointerdown` (see `AddStarter`). */
  startAddRef: { current: AddStarter | null };
  settings: CanvasSettings;
  /** Canvas zoom factor. The grid renders in layout pixels inside a
   * `scale(zoom)` layer, so every pointer delta must be divided by this to
   * land back in layout space (drag, resize, palette add). */
  zoom?: number;
  /** Currently selected cards. Owned by the view (the inspector is a sibling). */
  selectedIds?: readonly string[];
  onSelectionChange?: (ids: string[]) => void;
  /** True while an undo/redo is being applied — see `handleLayoutChange`. */
  historyApplying?: () => boolean;
  /** Geometry changes. The second argument labels the edit for the undo stack. */
  onItemsChange: (items: DashboardItem[], label?: string) => void;
}) {
  const grid = GRID;
  const cols = GRID.cols;
  // Below the stack threshold the board is a single column and read-only, so
  // no gesture may start (see `stacked` below).
  const wrapRef = useRef<HTMLDivElement>(null);
  const containerWidth = useContainerWidth(wrapRef);
  const stacked = containerWidth > 0 && containerWidth < STACK_BELOW_PX;
  const freeReflow = editing && !stacked;
  // Gestures read the zoom through a ref so a mid-gesture zoom change (ctrl+
  // wheel while dragging) can't leave a stale factor in the closures.
  const zoomRef = useRef(zoom);
  zoomRef.current = zoom;
  // Zoom changes shift every card's layout position (the width the columns are
  // computed from changes); suppress the per-item transform transition while
  // the wheel is spinning or the reflow shears against the scale change.
  const [zooming, setZooming] = useState(false);
  const zoomSettleRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const prevZoomRef = useRef(zoom);
  useEffect(() => {
    if (prevZoomRef.current === zoom) return;
    prevZoomRef.current = zoom;
    setZooming(true);
    if (zoomSettleRef.current) clearTimeout(zoomSettleRef.current);
    zoomSettleRef.current = setTimeout(() => setZooming(false), 200);
  }, [zoom]);
  useEffect(
    () => () => {
      if (zoomSettleRef.current) clearTimeout(zoomSettleRef.current);
    },
    [],
  );

  // Suppress RGL's one-time mount slide: WidthProvider first lays the cards out
  // at its 1280px default, then reflows to the measured width, and the CSS
  // transition animates that gap. Disable the transition until the reflow has
  // settled (two frames), then restore it so drag/resize stay animated.
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    let raf2 = 0;
    const raf1 = requestAnimationFrame(() => {
      raf2 = requestAnimationFrame(() => setMounted(true));
    });
    return () => {
      cancelAnimationFrame(raf1);
      if (raf2) cancelAnimationFrame(raf2);
    };
  }, []);
  // The catalog carries each card's `config_schema`; a placed item only stores
  // chosen values, so we look the schema up by `(module_id, widget_id)` to
  // render its settings form. Cached by react-query (the palette fetches it).
  const { data: catalog } = useCardCatalog();
  const schemaFor = useMemo(() => {
    const map = new Map<string, WidgetConfigSchema | null>();
    for (const c of catalog ?? []) map.set(`${c.module_id}:${c.widget_id}`, c.config_schema);
    // viz cards drive their settings from the viz registry + dataset columns
    // (see VizSettings), not a widget config_schema.
    return (it: DashboardItem) =>
      it.kind === "viz" ? undefined : map.get(`${it.module_id}:${it.widget_id}`);
  }, [catalog]);
  // Each card's manifest description — surfaced by the card header's ⓘ tooltip.
  // A placed item only stores its identity, so (like `schemaFor`) we resolve the
  // description from the catalog: widget cards by `(module_id, widget_id)`, viz
  // cards by their dataset ref `(module_id, dataset_id)`.
  // Structured help + drill target, resolved from the catalog the same way as
  // the description (a placed item only stores its identity). Drill applies to
  // widget cards only — a viz card renders a platform visualization of a
  // dataset, so there's no module view behind it to open.
  const cardMetaFor = useMemo(() => {
    const map = new Map<string, CatalogCard>();
    for (const c of catalog ?? []) map.set(`${c.module_id}:${c.widget_id}`, c);
    return (it: DashboardItem) =>
      it.kind === "viz" ? undefined : map.get(`${it.module_id}:${it.widget_id}`);
  }, [catalog]);
  // The header's "open in module" href. Uses the same resolver as `onDrill`,
  // so the button and a click inside the widget can never point at different
  // places. `null` ⇒ nowhere to go (no bound log, or drilling disabled), and
  // the header renders a disabled affordance rather than a dead link.
  const drillHrefFor = useCallback(
    (it: DashboardItem): string | null => {
      if (it.kind === "viz" || !it.module_id) return null;
      return resolveDrillHref({
        moduleId: it.module_id,
        logId,
        manifestDrill: cardMetaFor(it)?.drill,
      });
    },
    [logId, cardMetaFor],
  );
  const { data: datasetCatalog } = useDatasetCatalog();
  const descriptionFor = useMemo(() => {
    const widgets = new Map<string, string | null>();
    for (const c of catalog ?? []) widgets.set(`${c.module_id}:${c.widget_id}`, c.description);
    const datasets = new Map<string, string | null>();
    for (const d of datasetCatalog ?? [])
      datasets.set(`${d.module_id}:${d.dataset_id}`, d.description);
    return (it: DashboardItem): string | null => {
      if (it.kind === "viz") {
        const ref = it.dataset_ref;
        return (ref ? datasets.get(`${ref.module_id}:${ref.dataset_id}`) : null) ?? null;
      }
      return widgets.get(`${it.module_id}:${it.widget_id}`) ?? null;
    };
  }, [catalog, datasetCatalog]);
  // Per-widget grid constraints from the catalog, keyed by `module:widget`:
  //  - `resizable`       whether the user may resize the card at all,
  //  - `minW`/`minH`     the resize floor for a resizable card (and the size an
  //                      under-sized placed card is grown to on load),
  //  - `fixedW`/`fixedH` the locked size used when the card is NOT resizable.
  // Every field MUST resolve to a positive number — RGL treats a missing min as
  // `1` (GridItem default) — so coerce per-field to the historical floor when the
  // catalog hasn't loaded, predates the field, or no longer lists the card.
  //
  // A widget declares its minimum twice: in grid units (`min_w`/`min_h`) and as
  // absolute pixels (`min_px_w`/`min_px_h`). The pixel floors are the ones that
  // actually hold a card above its usable size — a grid unit is only a real
  // size once the container width is known, which is why this memo depends on
  // the measured width. We take whichever floor is larger.
  const constraintsFor = useMemo(() => {
    const FALLBACK = { resizable: true, minW: 2, minH: 3, fixedW: 6, fixedH: 8 };
    const num = (v: unknown, fallback: number) =>
      typeof v === "number" && Number.isFinite(v) && v > 0 ? v : fallback;
    // Width of one column, mirroring RGL's own calcGridColWidth: the container
    // pads by `margin` on both sides and gaps by `margin` between columns.
    const [marginX] = GRID.margin;
    const colWidth =
      containerWidth > 0 ? (containerWidth - marginX * (cols + 1)) / cols : 0;
    /** Absolute pixel width -> the smallest column count that covers it. */
    const colsForPx = (px: number) =>
      colWidth > 0 && px > 0 ? Math.ceil((px + marginX) / (colWidth + marginX)) : 0;
    const map = new Map<string, typeof FALLBACK>();
    for (const c of catalog ?? [])
      map.set(`${c.module_id}:${c.widget_id}`, {
        resizable: c.resizable !== false,
        minW: Math.min(
          cols,
          Math.max(num(c.min_w, FALLBACK.minW), colsForPx(c.min_px_w ?? 0)),
        ),
        minH: Math.max(num(c.min_h, FALLBACK.minH), rowsForPx(c.min_px_h ?? 0)),
        fixedW: num(c.default_w, FALLBACK.fixedW),
        fixedH: num(c.default_h, FALLBACK.fixedH),
      });
    return (it: DashboardItem) => {
      // viz cards are always resizable; their floor comes from the viz registry
      // (grid units only — the registry has no pixel floors to fold in).
      if (it.kind === "viz") {
        const d = it.viz_id ? vizRegistry[it.viz_id]?.defaults : undefined;
        return d
          ? {
              resizable: true,
              minW: Math.min(cols, d.minW),
              minH: d.minH,
              fixedW: d.w,
              fixedH: d.h,
            }
          : FALLBACK;
      }
      return map.get(`${it.module_id}:${it.widget_id}`) ?? FALLBACK;
    };
  }, [catalog, cols, containerWidth]);

  // Live free-mode drag state. `liveItems` overrides the rendered layout while a
  // drag is in flight; `draggingId` marks the card whose transition is killed so
  // it tracks the cursor instead of easing behind it.
  const [liveItems, setLiveItems] = useState<DashboardItem[] | null>(null);
  const [draggingId, setDraggingId] = useState<string | null>(null);
  const [resizingId, setResizingId] = useState<string | null>(null);
  // Live palette→canvas add state. `addReq` is the item being added - a widget
  // card or a dataset viz (drives the ghost + chip); `addPointer` positions the
  // chip; `addCell` is the snapped grid cell under the cursor (null when off-grid).
  const [addReq, setAddReq] = useState<AddRequest | null>(null);
  const [addPointer, setAddPointer] = useState<{ x: number; y: number } | null>(null);
  const [addCell, setAddCell] = useState<{ x: number; y: number } | null>(null);
  // Card lifecycle animation state. `recentlyAddedId` pops the just-dropped card
  // in; `removingIds` plays the exit animation while the card lingers one tick
  // before it's actually dropped from `items` (others then reflow into the gap).
  const reduceMotion = useReducedMotion();
  const [recentlyAddedId, setRecentlyAddedId] = useState<string | null>(null);
  const [removingIds, setRemovingIds] = useState<Set<string>>(() => new Set());
  const dragRef = useRef<FreeDrag | null>(null);
  const resizeRef = useRef<FreeResize | null>(null);
  const liveRef = useRef<DashboardItem[] | null>(null);
  // Detach in-flight drag/add listeners (and pending lifecycle timers) on unmount.
  const teardownRef = useRef<() => void>(() => {});
  const addTeardownRef = useRef<() => void>(() => {});
  const timersRef = useRef<Set<ReturnType<typeof setTimeout>>>(new Set());
  const schedule = useCallback((fn: () => void, ms: number) => {
    const t = setTimeout(() => {
      timersRef.current.delete(t);
      fn();
    }, ms);
    timersRef.current.add(t);
  }, []);
  useEffect(
    () => () => {
      teardownRef.current();
      addTeardownRef.current();
      timersRef.current.forEach(clearTimeout);
      timersRef.current.clear();
    },
    [],
  );

  // The live placeholder while adding from the palette: a ghost item at the
  // hovered cell that real cards reflow around (same machinery as a free drag),
  // so the preview matches exactly what `commitAdd` will persist.
  const ghostItem = useMemo<DashboardItem | null>(() => {
    if (!addReq || !addCell) return null;
    return buildAddItem(addReq, ADD_GHOST_ID, addCell.x, addCell.y);
  }, [addReq, addCell]);

  // Cards render from the committed `items` (stable content) and are positioned
  // by the `layout` prop, which carries live positions during a free drag or a
  // palette add. That split keeps widget bodies from re-rendering on every move.
  const displayItems = useMemo<DashboardItem[]>(
    () =>
      ghostItem
        ? reflowFree([...items, ghostItem], ADD_GHOST_ID, ghostItem.x, ghostItem.y)
        : (liveItems ?? items),
    [ghostItem, liveItems, items],
  );
  const layout = useMemo<Layout[]>(
    () =>
      displayItems.map((it) => {
        const c = constraintsFor(it);
        if (!c.resizable) {
          // Fixed-size card: lock to the declared size and forbid resize. The
          // stored w/h is ignored so even an old placement renders at the fixed
          // size; in edit mode RGL's mount `onLayoutChange` persists it.
          return {
            i: it.i,
            x: it.x,
            y: it.y,
            w: c.fixedW,
            h: c.fixedH,
            minW: c.fixedW,
            maxW: c.fixedW,
            minH: c.fixedH,
            maxH: c.fixedH,
            isResizable: false,
          };
        }
        // Resizable card: RGL only enforces minW/minH during an interactive
        // resize – it never grows an already-placed item on load. So a card
        // stored below its minimum would keep rendering too small. Clamp w/h up
        // to the minimum here so the floor is applied to the rendered size too;
        // in edit mode RGL's mount `onLayoutChange` persists the corrected size.
        return {
          i: it.i,
          x: it.x,
          y: it.y,
          w: Math.max(it.w, c.minW),
          h: Math.max(it.h, c.minH),
          minW: c.minW,
          minH: c.minH,
        };
      }),
    [displayItems, constraintsFor],
  );

  // Stable refs/handlers so memoized cards don't re-render while dragging.
  const itemsRef = useRef(items);
  itemsRef.current = items;
  const onItemsChangeRef = useRef(onItemsChange);
  onItemsChangeRef.current = onItemsChange;
  const selectedIdsRef = useRef(selectedIds);
  selectedIdsRef.current = selectedIds;
  const selectedSet = useMemo(() => new Set(selectedIds), [selectedIds]);
  const onSelectionChangeRef = useRef(onSelectionChange);
  onSelectionChangeRef.current = onSelectionChange;
  const updateItem = useCallback(
    (id: string, patch: CardPatch) =>
      onItemsChangeRef.current(
        itemsRef.current.map((it) => (it.i === id ? { ...it, ...patch } : it)),
        "config",
      ),
    [],
  );
  const removeItem = useCallback(
    (id: string) =>
      onItemsChangeRef.current(itemsRef.current.filter((it) => it.i !== id), "remove"),
    [],
  );
  // Play the card's exit animation, then drop it from `items` a tick later so the
  // others reflow into the freed space. Under reduced motion, remove immediately.
  const requestRemove = useCallback(
    (id: string) => {
      if (reduceMotion) {
        removeItem(id);
        return;
      }
      setRemovingIds((prev) => new Set(prev).add(id));
      schedule(() => {
        removeItem(id);
        setRemovingIds((prev) => {
          const next = new Set(prev);
          next.delete(id);
          return next;
        });
      }, 170);
    },
    [reduceMotion, removeItem, schedule],
  );
  const cardHandlers = useMemo(() => {
    const m = new Map<string, { onUpdate: (p: CardPatch) => void; onRemove: () => void }>();
    for (const it of items)
      m.set(it.i, { onUpdate: (p) => updateItem(it.i, p), onRemove: () => requestRemove(it.i) });
    return m;
  }, [items, updateItem, requestRemove]);

  // Drill handlers, cached per card id and NEVER rebuilt.
  //
  // This one has to be identity-stable in a way the handlers above don't:
  // `onDrill` is passed into the module's own widget component, so a fresh
  // function on every render would re-render every widget body on every drag
  // commit — remounting charts mid-drag. The changing values (bound log, the
  // catalog entry) are read through refs at call time instead of captured.
  const router = useRouter();
  const logIdRef = useRef(logId);
  logIdRef.current = logId;
  const cardMetaRef = useRef(cardMetaFor);
  cardMetaRef.current = cardMetaFor;
  const routerRef = useRef(router);
  routerRef.current = router;
  const drillCache = useRef(new Map<string, DrillHandler>());
  const drillFor = useCallback((id: string): DrillHandler => {
    const cached = drillCache.current.get(id);
    if (cached) return cached;
    const handler: DrillHandler = (target) => {
      const it = itemsRef.current.find((c) => c.i === id);
      if (!it || !it.module_id) return;
      const href = resolveDrillHref(
        {
          moduleId: it.module_id,
          logId: logIdRef.current,
          manifestDrill: cardMetaRef.current(it)?.drill,
        },
        target,
      );
      if (href) routerRef.current.push(href);
    };
    drillCache.current.set(id, handler);
    return handler;
  }, []);
  // Drop handlers for removed cards so the cache can't grow across a long
  // editing session.
  useEffect(() => {
    const live = new Set(items.map((it) => it.i));
    for (const id of drillCache.current.keys()) {
      if (!live.has(id)) drillCache.current.delete(id);
    }
  }, [items]);

  const handleLayoutChange = (next: Layout[]) => {
    // RGL fires this on mount, on resize, and on its own (non-free) drags. While
    // a free drag/resize or palette add is in flight the layout prop is ours (it
    // carries the ghost/reflow), so ignore the echo or we'd persist the preview.
    // `historyApplying` is the load-bearing one. An undo swaps `items`, the
    // layout re-derives, and RGL echoes it straight back here — recorded as a
    // fresh edit, that would push the state you just undid back onto the stack
    // and make undo unreachable.
    if (!editing || stacked || dragRef.current || resizeRef.current || addReq) return;
    if (historyApplying?.()) return;
    const byId = new Map(next.map((l) => [l.i, l]));
    const merged = items.map((it) => {
      const l = byId.get(it.i);
      return l ? { ...it, x: l.x, y: l.y, w: l.w, h: l.h } : it;
    });
    // Only propagate if geometry actually changed (RGL fires on mount too).
    const changed = merged.some(
      (m, idx) =>
        m.x !== items[idx].x ||
        m.y !== items[idx].y ||
        m.w !== items[idx].w ||
        m.h !== items[idx].h,
    );
    if (changed) onItemsChange(merged, "layout");
  };

  // Free-mode pointer gestures: move (drag handle) and resize (corner handle).
  // Both make the active card the fixed obstacle and reflow the rest via
  // `reflowFree`, so the manipulated card has priority and others yield — RGL's
  // native drag/resize (which instead clamps a growing card against its
  // neighbours) is disabled in free mode. Bubble phase (not capture) so the
  // header's control buttons, which `stopPropagation`, never start a gesture.
  const onPointerDown = (e: React.PointerEvent) => {
    if (!freeReflow || dragRef.current || resizeRef.current) return;
    const target = e.target as HTMLElement;
    const cardEl = target.closest("[data-grid-id]") as HTMLElement | null;
    const id = cardEl?.dataset.gridId;

    // Empty canvas: clear the selection. Doing this on pointerdown (not click)
    // keeps it consistent with how selection is made.
    if (!id) {
      if (selectedIdsRef.current.length > 0) onSelectionChange?.([]);
      return;
    }
    if (id === ADD_GHOST_ID) return;
    const it = itemsRef.current.find((i) => i.i === id);
    if (!it) return;

    // Select before any gesture starts, so a drag always moves what the user
    // sees highlighted. Shift/meta toggles; a plain press on an already-
    // selected card keeps the whole selection (so you can drag several).
    const current = selectedIdsRef.current;
    if (e.shiftKey || e.metaKey || e.ctrlKey) {
      onSelectionChange?.(
        current.includes(id) ? current.filter((x) => x !== id) : [...current, id],
      );
    } else if (!current.includes(id)) {
      onSelectionChange?.([id]);
    }
    const gridEl = wrapRef.current?.querySelector(".react-grid-layout") as HTMLElement | null;
    const width = gridEl?.clientWidth ?? wrapRef.current?.clientWidth ?? 0;
    if (!width) return;

    // RGL geometry: containerPadding defaults to margin, so both pad and gap are
    // `margin`. Mirror calcGridColWidth/calcXY so snapping matches RGL exactly.
    const [marginX, marginY] = grid.margin;
    const colW = (width - marginX * (cols - 1) - marginX * 2) / cols;
    const stepX = colW + marginX;
    const stepY = grid.rowHeight + marginY;

    // Resize: the corner handle. The card keeps its (x, y); only w/h grow, and
    // the cards it now overlaps reflow below it.
    if (target.closest(".dashboard-resize-handle")) {
      const c = constraintsFor(it);
      if (!c.resizable) return;
      const { minW, minH } = c;
      resizeRef.current = {
        id,
        pointerX: e.clientX,
        pointerY: e.clientY,
        stepX,
        stepY,
        x: it.x,
        y: it.y,
        startW: it.w,
        startH: it.h,
        minW,
        minH,
        cols,
        snapshot: itemsRef.current,
      };
      liveRef.current = itemsRef.current;
      setResizingId(id);
      setLiveItems(itemsRef.current);
      e.preventDefault();

      const move = (ev: PointerEvent) => {
        const r = resizeRef.current;
        if (!r) return;
        // Pointer deltas are visual px; the grid steps are layout px (÷ zoom).
        const z = zoomRef.current;
        const w = Math.max(
          r.minW,
          Math.min(r.cols - r.x, r.startW + Math.round((ev.clientX - r.pointerX) / z / r.stepX)),
        );
        const h = Math.max(r.minH, r.startH + Math.round((ev.clientY - r.pointerY) / z / r.stepY));
        const baseline = r.snapshot.map((s) => (s.i === r.id ? { ...s, w, h } : s));
        const nextLayout = reflowFree(baseline, r.id, r.x, r.y);
        liveRef.current = nextLayout;
        setLiveItems(nextLayout);
      };
      const end = (commit: boolean) => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        window.removeEventListener("pointercancel", cancel);
        teardownRef.current = () => {};
        const final = liveRef.current;
        resizeRef.current = null;
        liveRef.current = null;
        setResizingId(null);
        setLiveItems(null);
        if (commit && final) {
          const changed = final.some((m) => {
            const o = itemsRef.current.find((i) => i.i === m.i);
            return !o || o.x !== m.x || o.y !== m.y || o.w !== m.w || o.h !== m.h;
          });
          if (changed) onItemsChangeRef.current(final, "resize");
        }
      };
      const up = () => end(true);
      const cancel = () => end(false);
      teardownRef.current = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        window.removeEventListener("pointercancel", cancel);
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
      window.addEventListener("pointercancel", cancel);
      return;
    }

    // Move: the drag handle (card header).
    if (!target.closest(".dashboard-drag-handle")) return;
    dragRef.current = {
      id,
      pointerX: e.clientX,
      pointerY: e.clientY,
      startLeft: marginX + it.x * stepX,
      startTop: marginY + it.y * stepY,
      stepX,
      stepY,
      padX: marginX,
      padY: marginY,
      w: it.w,
      cols,
      snapshot: itemsRef.current,
    };
    liveRef.current = itemsRef.current;
    setDraggingId(id);
    setLiveItems(itemsRef.current);
    e.preventDefault();

    // Everything selected moves together. Captured at gesture start so the
    // group can't change mid-drag.
    const groupIds = selectedIdsRef.current.includes(id)
      ? selectedIdsRef.current.filter((gid) => itemsRef.current.some((c) => c.i === gid))
      : [id];

    const move = (ev: PointerEvent) => {
      const d = dragRef.current;
      if (!d) return;
      // Pointer deltas are visual px; start/step are layout px (÷ zoom).
      const z = zoomRef.current;
      const left = d.startLeft + (ev.clientX - d.pointerX) / z;
      const top = d.startTop + (ev.clientY - d.pointerY) / z;
      const x = Math.max(0, Math.min(d.cols - d.w, Math.round((left - d.padX) / d.stepX)));
      const y = Math.max(0, Math.round((top - d.padY) / d.stepY));
      // Drive the group by the DELTA of the card actually under the cursor, so
      // the others keep their relative offsets instead of stacking on it.
      const nextLayout =
        groupIds.length > 1
          ? reflowDelta(d.snapshot, groupIds, x - it.x, y - it.y, d.cols)
          : reflowFree(d.snapshot, d.id, x, y);
      liveRef.current = nextLayout;
      setLiveItems(nextLayout);
    };
    const end = (commit: boolean) => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", cancel);
      teardownRef.current = () => {};
      const final = liveRef.current;
      dragRef.current = null;
      liveRef.current = null;
      setDraggingId(null);
      setLiveItems(null);
      if (commit && final) {
        const changed = final.some((m) => {
          const o = itemsRef.current.find((i) => i.i === m.i);
          return !o || o.x !== m.x || o.y !== m.y;
        });
        if (changed) onItemsChangeRef.current(final, "move");
      }
    };
    const up = () => end(true);
    const cancel = () => end(false);
    teardownRef.current = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", cancel);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", cancel);
  };

  // Append `card` and reflow existing cards around it (same as the live ghost
  // preview), then hand the new list to the parent. Refs so it's stable for the
  // gesture effect below.
  const commitAdd = useCallback((req: AddRequest, x: number, y: number) => {
    const newItem = buildAddItem(req, newCardId(), x, y);
    onItemsChangeRef.current(
      reflowFree([...itemsRef.current, newItem], newItem.i, x, y),
      "add",
    );
    // Pop the new card in; clear the flag after the animation (no-op under
    // reduced motion, where the CSS guard strips the animation duration).
    setRecentlyAddedId(newItem.i);
    schedule(() => setRecentlyAddedId((cur) => (cur === newItem.i ? null : cur)), 260);
  }, [schedule]);

  // Snap a viewport point to a grid cell, mirroring RGL's calcXY (the cursor is
  // the new card's top-left). Returns null when the point is outside the canvas,
  // which the add-gesture treats as "not a drop target".
  const cellWithinGrid = useCallback(
    (clientX: number, clientY: number, w: number) => {
      const wrapEl = wrapRef.current;
      if (!wrapEl) return null;
      // Bound the droppable area by the wrapper, NOT `.react-grid-layout`: the
      // grid collapses to its content height, so on an EMPTY board its rect is
      // ~0px tall and every point reads as "below the grid". The wrapper is
      // reliably full-height (`min-h-full`), so it covers the visible canvas.
      const bounds = wrapEl.getBoundingClientRect();
      if (
        clientX < bounds.left ||
        clientX > bounds.right ||
        clientY < bounds.top ||
        clientY > bounds.bottom
      )
        return null;
      // Snap against the grid's own origin/width so it matches RGL exactly (its
      // left/top/width stay correct even when its height has collapsed); fall
      // back to the wrapper if the grid node isn't mounted yet.
      const gridEl = wrapEl.querySelector(".react-grid-layout") as HTMLElement | null;
      const rect = gridEl?.getBoundingClientRect() ?? bounds;
      // clientWidth is layout px (transforms don't affect it); the pointer's
      // offset within the scaled rect is visual px, so divide it by zoom to get
      // back into the same layout space before snapping.
      const width = gridEl?.clientWidth || rect.width;
      if (!width) return null;
      const z = zoomRef.current;
      const [mx, my] = grid.margin;
      const colW = (width - mx * (cols - 1) - mx * 2) / cols;
      const px = (clientX - rect.left) / z;
      const py = (clientY - rect.top) / z;
      const x = Math.max(0, Math.min(cols - w, Math.round((px - mx) / (colW + mx))));
      const y = Math.max(0, Math.round((py - my) / (grid.rowHeight + my)));
      return { x, y };
    },
    [grid, cols],
  );

  // Palette → canvas add. The palette calls `startAdd` synchronously from its
  // `pointerdown` (via `startAddRef`), so the drag listeners attach in the same
  // tick as the press — identical to the canvas's own free-drag (`onPointerDown`
  // below), not deferred through React state + an effect. We track the cursor to
  // preview a ghost, then on release drop at the hovered cell — or, if it was a
  // click with no real movement, append at the bottom so a plain click still adds.
  const startAdd = useCallback(
    (req: AddRequest, e: React.PointerEvent) => {
      if (!editing) return;
      const startX = e.clientX;
      const startY = e.clientY;
      const { w, h } = addGeometry(req);
      setAddReq(req);
      setAddPointer({ x: startX, y: startY });
      setAddCell(cellWithinGrid(startX, startY, w));
      let moved = false;
      const move = (ev: PointerEvent) => {
        if (Math.abs(ev.clientX - startX) > 4 || Math.abs(ev.clientY - startY) > 4) moved = true;
        setAddPointer({ x: ev.clientX, y: ev.clientY });
        setAddCell(cellWithinGrid(ev.clientX, ev.clientY, w));
      };
      const finish = (ev: PointerEvent) => {
        cleanup();
        const cell = cellWithinGrid(ev.clientX, ev.clientY, w);
        if (cell) commitAdd(req, cell.x, cell.y);
        else if (!moved) {
          // No drag + off-grid release = a plain palette click. Place the card as
          // high as it fits (filling a gap at the top, e.g. right of the first row)
          // rather than always appending at the bottom.
          const { x, y } = findTopFit(itemsRef.current, w, h, cols);
          commitAdd(req, x, y);
        }
      };
      const cancel = () => cleanup();
      const cleanup = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", finish);
        window.removeEventListener("pointercancel", cancel);
        addTeardownRef.current = () => {};
        setAddReq(null);
        setAddPointer(null);
        setAddCell(null);
      };
      addTeardownRef.current = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", finish);
        window.removeEventListener("pointercancel", cancel);
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", finish);
      window.addEventListener("pointercancel", cancel);
    },
    [editing, cellWithinGrid, commitAdd, cols],
  );

  // Publish the starter so the palette (a sibling) can begin the gesture from its
  // own `pointerdown`, synchronously.
  useEffect(() => {
    startAddRef.current = startAdd;
    return () => {
      startAddRef.current = null;
    };
  }, [startAdd, startAddRef]);

  // Keyboard editing for the current selection. Pointer-only editing meant
  // nudging a card one cell required a steady hand; these are the operations
  // that are genuinely faster from the keyboard.
  useEffect(() => {
    if (!freeReflow) return;
    const onKeyDown = (e: KeyboardEvent) => {
      // Never hijack typing. Covers inputs, textareas, contenteditable, and
      // anything inside a Radix popover (the card settings form lives there).
      const el = document.activeElement as HTMLElement | null;
      if (
        el &&
        (el.isContentEditable ||
          ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) ||
          el.closest("[data-radix-popper-content-wrapper]"))
      ) {
        return;
      }

      const ids = selectedIdsRef.current;
      const mod = e.metaKey || e.ctrlKey;

      if (e.key === "Escape") {
        if (ids.length) onSelectionChangeRef.current?.([]);
        return;
      }
      // Select-all is only meaningful while editing.
      if (mod && e.key.toLowerCase() === "a") {
        e.preventDefault();
        onSelectionChangeRef.current?.(itemsRef.current.map((it) => it.i));
        return;
      }
      if (!ids.length) return;

      if (mod && e.key.toLowerCase() === "d") {
        e.preventDefault();
        const copies: DashboardItem[] = [];
        for (const id of ids) {
          const src = itemsRef.current.find((c) => c.i === id);
          if (!src) continue;
          copies.push({
            ...src,
            i: newCardId(),
            y: src.y + src.h,
            // Deep-clone so editing the copy's options can't mutate the
            // original's (both would otherwise share one object).
            config: structuredClone(src.config),
            mapping: src.mapping ? structuredClone(src.mapping) : src.mapping,
          });
        }
        if (!copies.length) return;
        const next = [...itemsRef.current, ...copies];
        onItemsChangeRef.current(
          reflowPinned(next, new Map(copies.map((c) => [c.i, { x: c.x, y: c.y }]))),
          "duplicate",
        );
        onSelectionChangeRef.current?.(copies.map((c) => c.i));
        return;
      }

      if (e.key === "Delete" || e.key === "Backspace") {
        e.preventDefault();
        const remove = new Set(ids);
        onItemsChangeRef.current(
          itemsRef.current.filter((it) => !remove.has(it.i)),
          "remove",
        );
        onSelectionChangeRef.current?.([]);
        return;
      }

      const NUDGE: Record<string, [number, number]> = {
        ArrowLeft: [-1, 0],
        ArrowRight: [1, 0],
        ArrowUp: [0, -1],
        ArrowDown: [0, 1],
      };
      const delta = NUDGE[e.key];
      if (!delta) return;
      e.preventDefault();
      // Shift jumps in fours — a whole card-width step on a 12-column grid.
      const scale = e.shiftKey ? 4 : 1;
      onItemsChangeRef.current(
        reflowDelta(itemsRef.current, ids, delta[0] * scale, delta[1] * scale, cols),
        // One label so a burst of nudges coalesces into a single undo step
        // instead of filling the stack one cell at a time.
        "nudge",
      );
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [freeReflow, cols]);

  // The zoom layer scales the grid down/up around the top-left corner. Width is
  // widened by 1/zoom so the scaled result still fills the viewport exactly
  // (WidthProvider remeasures via ResizeObserver); the wrapper gets the scaled
  // content height so the scroll range matches what's visible (a bare CSS
  // transform never shrinks the layout box). min-h-full keeps the empty-board
  // area a full-height drop target either way.
  const gridH = gridPixelHeight(displayItems, grid);

  // Narrow viewport: 12 columns of ~50px can't hold a real card, so drop the
  // grid entirely and stack in reading order at each card's natural height
  // (never below its pixel floor). Read-only — `freeReflow` is already false
  // when stacked, so no gesture can start and geometry can't be edited into a
  // state the user can't see.
  if (stacked) {
    const ordered = [...items].sort((a, b) => a.y - b.y || a.x - b.x);
    return (
      <div ref={wrapRef} className="flex min-h-full flex-col gap-2">
        {ordered.map((it) => {
          const c = constraintsFor(it);
          return (
            <div
              key={it.i}
              data-grid-id={it.i}
              style={{ height: rowsToPx(Math.max(it.h, c.minH)) }}
            >
              <DashboardCard
                item={it}
                logId={logId}
                editing={false}
                schema={schemaFor(it)}
                description={descriptionFor(it)}
                help={cardMetaFor(it)?.help}
                drillHref={drillHrefFor(it)}
                drillLabel={drillLabel(cardMetaFor(it)?.drill)}
                onDrill={drillFor(it.i)}
                chrome={settings.chrome}
                onUpdate={() => {}}
                onRemove={() => {}}
              />
            </div>
          );
        })}
      </div>
    );
  }

  return (
    <div
      ref={wrapRef}
      className="relative min-h-full"
      style={{ height: Math.round(gridH * zoom) }}
      onPointerDown={freeReflow ? onPointerDown : undefined}
    >
      <div
        className="dashboard-zoom-layer"
        data-editing={editing || undefined}
        style={{
          transform: zoom !== 1 ? `scale(${zoom})` : undefined,
          width: zoom !== 1 ? `${100 / zoom}%` : undefined,
          minHeight: `${100 / zoom}%`,
        }}
      >
        <GridLayout
          className={cn("min-h-full", !mounted && "rgl-mounting", zooming && "rgl-zooming")}
          layout={layout}
          cols={cols}
          rowHeight={grid.rowHeight}
          margin={grid.margin}
          // RGL is a positioning engine here, nothing more. Its own drag and
          // resize stay off because they can't express this board's model: RGL
          // refuses to move non-dragged items from the `layout` prop mid-drag,
          // and with no compaction it never springs pushed cards back. Both
          // gestures are driven by `onPointerDown` above via `reflowFree`,
          // which also already handles zoom, the palette add-ghost and touch.
          isDraggable={false}
          isResizable={false}
          draggableHandle=".dashboard-drag-handle"
          onLayoutChange={handleLayoutChange}
          // Never auto-compact: cards stay exactly where they are placed.
          compactType={null}
          transformScale={zoom}
        >
        {items.map((it) => {
          const h = cardHandlers.get(it.i);
          return (
            <div
              key={it.i}
              data-grid-id={it.i}
              data-selected={selectedSet.has(it.i) || undefined}
              className={cn(
                "group",
                draggingId === it.i && "rgl-free-dragging",
                resizingId === it.i && "rgl-free-resizing",
              )}
            >
              {/* Inner wrapper carries the add/remove animation (transform/opacity)
                  so it never fights RGL's `transform` on the grid-item above.
                  `fill-mode-forwards` on the exit is mandatory: the card outlives
                  its animation by a tick, and without it the faded-out state is
                  discarded on animation end and the card flashes back at full
                  opacity for a frame before it's dropped. */}
              <div
                className={cn(
                  "h-full",
                  recentlyAddedId === it.i && "animate-in fade-in-0 zoom-in-95 duration-200",
                  removingIds.has(it.i) &&
                    "animate-out fade-out-0 zoom-out-95 fill-mode-forwards pointer-events-none duration-150",
                )}
              >
                <DashboardCard
                  item={it}
                  logId={logId}
                  editing={editing}
                  schema={schemaFor(it)}
                  description={descriptionFor(it)}
                  help={cardMetaFor(it)?.help}
                  drillHref={drillHrefFor(it)}
                  drillLabel={drillLabel(cardMetaFor(it)?.drill)}
                  onDrill={drillFor(it.i)}
                  chrome={settings.chrome}
                  onUpdate={h?.onUpdate ?? (() => {})}
                  onRemove={h?.onRemove ?? (() => {})}
                />
              </div>
              {freeReflow && constraintsFor(it).resizable && (
                // Custom resize grip (RGL's native resize is off in free mode):
                // `onPointerDown` reads `.dashboard-resize-handle` to start a
                // reflow-driven resize. Clickable even at opacity 0. Omitted for
                // fixed-size (non-resizable) cards.
                <div
                  className="dashboard-resize-handle absolute bottom-0.5 right-0.5 z-[4] h-3.5 w-3.5 cursor-se-resize touch-none rounded-[2px] border-b-2 border-r-2 border-muted-foreground/40 opacity-0 transition-opacity group-hover:opacity-100"
                  aria-hidden
                />
              )}
            </div>
          );
        })}
          {ghostItem && (
            <div key={ADD_GHOST_ID} data-grid-id={ADD_GHOST_ID} className="pointer-events-none">
              <div className="dashboard-add-ghost h-full w-full rounded-lg" />
            </div>
          )}
        </GridLayout>
      </div>
      {addReq &&
        addPointer &&
        createPortal(
          <div
            className="pointer-events-none fixed z-50 max-w-[16rem] truncate rounded-md border border-border bg-card px-2 py-1 text-xs font-medium shadow-lg animate-in fade-in-0 zoom-in-95 duration-150"
            style={{ left: addPointer.x + 12, top: addPointer.y + 12 }}
          >
            {addReq.kind === "widget" ? addReq.card.title : addReq.dataset.title}
          </div>,
          document.body,
        )}
    </div>
  );
}
