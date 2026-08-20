"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useReducedMotion } from "framer-motion";
import RGL, { WidthProvider, type Layout } from "react-grid-layout";

import { cn } from "@/lib/cn";
import { DashboardCard } from "@/components/dashboards/dashboard-card";
import {
  configDefaults,
  GRANULARITY,
  useCardCatalog,
  type CanvasSettings,
  type DashboardCard as CatalogCard,
  type DashboardItem,
  type WidgetConfigSchema,
} from "@/lib/dashboard-queries";

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
 * Non-compacting reflow. Places `id` at `(x, y)`, pushes *only* the cards it
 * (transitively) overlaps straight down, and leaves every other card exactly
 * where `snapshot` had it. It's recomputed from the drag-start snapshot on every
 * pointer move, so displaced cards spring back the instant the dragged card
 * leaves them – while intentional gaps survive (there's no vertical compaction,
 * unlike react-grid-layout's built-in `compactType: "vertical"`).
 */
function reflowFree(snapshot: DashboardItem[], id: string, x: number, y: number): DashboardItem[] {
  const result = snapshot.map((it) => ({ ...it }));
  const dragged = result.find((it) => it.i === id);
  if (!dragged) return result;
  dragged.x = x;
  dragged.y = y;
  // The dragged card is the fixed obstacle everyone yields to. Resolve the rest
  // in reading order, pushing each below whatever it lands on, then add it to the
  // obstacle set so later cards cascade off it too.
  const placed: DashboardItem[] = [dragged];
  const others = result
    .filter((it) => it.i !== id)
    .sort((a, b) => a.y - b.y || a.x - b.x);
  for (const it of others) {
    let guard = 0;
    let c: DashboardItem | null;
    while ((c = firstCollision(placed, it)) && guard++ < 1000) it.y = c.y + c.h;
    placed.push(it);
  }
  return result;
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

/** Synchronously begins a palette→canvas add at the pointer's position. The
 * canvas hands one of these to the palette (via a ref) so the palette's
 * `pointerdown` attaches the drag listeners in the same tick — identical to the
 * canvas's own free-drag, not deferred through React state + an effect. */
export type AddStarter = (card: CatalogCard, e: React.PointerEvent) => void;

/** Layout/child id of the live placeholder shown while adding from the palette. */
const ADD_GHOST_ID = "__add_ghost__";

/**
 * The react-grid-layout canvas. In edit mode it accepts adds from the palette
 * (via `startAddRef`), and drag/resize via the card header handle. Geometry
 * changes flow back through `onItemsChange`; the parent owns the item list. The
 * `settings.granularity` chooses the snap resolution (cols), row height, and
 * gutter – never auto-compaction, so cards stay exactly where you place them.
 *
 * Since no granularity compacts (`compactType: null`), drag is driven here
 * instead of by RGL: RGL won't let the layout prop move non-dragged cards
 * mid-drag, and its null-compaction never springs pushed cards back. So RGL's
 * own drag is disabled and a fully controlled layout is recomputed per pointer
 * move (see `reflowFree`). Palette adds are pointer-driven too (HTML5 drag-drop
 * never reaches the canvas behind the prod proxy); RGL still owns native resize.
 */
export function DashboardCanvas({
  items,
  logId,
  editing,
  startAddRef,
  settings,
  onItemsChange,
}: {
  items: DashboardItem[];
  logId: string | null;
  editing: boolean;
  /** The canvas publishes its add-drag starter here so the palette can call it
   * synchronously from `pointerdown` (see `AddStarter`). */
  startAddRef: { current: AddStarter | null };
  settings: CanvasSettings;
  onItemsChange: (items: DashboardItem[]) => void;
}) {
  const grid = GRANULARITY[settings.granularity] ?? GRANULARITY.medium;
  const cols = grid.cols;
  const freeReflow = editing && grid.compactType === null;

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
    return (moduleId: string, widgetId: string) => map.get(`${moduleId}:${widgetId}`);
  }, [catalog]);
  // Per-widget grid constraints from the catalog, keyed by `module:widget`:
  //  - `resizable`       whether the user may resize the card at all,
  //  - `minW`/`minH`     the resize floor for a resizable card (and the size an
  //                      under-sized placed card is grown to on load),
  //  - `fixedW`/`fixedH` the locked size used when the card is NOT resizable.
  // Every field MUST resolve to a positive number — RGL treats a missing min as
  // `1` (GridItem default) — so coerce per-field to the historical floor when the
  // catalog hasn't loaded, predates the field, or no longer lists the card.
  const constraintsFor = useMemo(() => {
    const FALLBACK = { resizable: true, minW: 2, minH: 3, fixedW: 6, fixedH: 8 };
    const num = (v: unknown, fallback: number) =>
      typeof v === "number" && Number.isFinite(v) && v > 0 ? v : fallback;
    const map = new Map<string, typeof FALLBACK>();
    for (const c of catalog ?? [])
      map.set(`${c.module_id}:${c.widget_id}`, {
        resizable: c.resizable !== false,
        minW: num(c.min_w, FALLBACK.minW),
        minH: num(c.min_h, FALLBACK.minH),
        fixedW: num(c.default_w, FALLBACK.fixedW),
        fixedH: num(c.default_h, FALLBACK.fixedH),
      });
    return (moduleId: string, widgetId: string) =>
      map.get(`${moduleId}:${widgetId}`) ?? FALLBACK;
  }, [catalog]);

  // Live free-mode drag state. `liveItems` overrides the rendered layout while a
  // drag is in flight; `draggingId` marks the card whose transition is killed so
  // it tracks the cursor instead of easing behind it.
  const [liveItems, setLiveItems] = useState<DashboardItem[] | null>(null);
  const [draggingId, setDraggingId] = useState<string | null>(null);
  const [resizingId, setResizingId] = useState<string | null>(null);
  // Live palette→canvas add state. `addCard` is the card being added (drives the
  // ghost + chip); `addPointer` positions the chip; `addCell` is the snapped grid
  // cell under the cursor (null when off-grid).
  const [addCard, setAddCard] = useState<CatalogCard | null>(null);
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
  const wrapRef = useRef<HTMLDivElement>(null);
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
    if (!addCard || !addCell) return null;
    return {
      i: ADD_GHOST_ID,
      module_id: addCard.module_id,
      widget_id: addCard.widget_id,
      title: addCard.title,
      x: addCell.x,
      y: addCell.y,
      w: addCard.default_w,
      h: addCard.default_h,
      config: {},
    };
  }, [addCard, addCell]);

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
        const c = constraintsFor(it.module_id, it.widget_id);
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
  const updateItem = useCallback(
    (id: string, patch: { title?: string; config?: Record<string, unknown> }) =>
      onItemsChangeRef.current(
        itemsRef.current.map((it) => (it.i === id ? { ...it, ...patch } : it)),
      ),
    [],
  );
  const removeItem = useCallback(
    (id: string) => onItemsChangeRef.current(itemsRef.current.filter((it) => it.i !== id)),
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
    const m = new Map<
      string,
      { onUpdate: (p: { title?: string; config?: Record<string, unknown> }) => void; onRemove: () => void }
    >();
    for (const it of items)
      m.set(it.i, { onUpdate: (p) => updateItem(it.i, p), onRemove: () => requestRemove(it.i) });
    return m;
  }, [items, updateItem, requestRemove]);

  const handleLayoutChange = (next: Layout[]) => {
    // RGL fires this on mount, on resize, and on its own (non-free) drags. While
    // a free drag/resize or palette add is in flight the layout prop is ours (it
    // carries the ghost/reflow), so ignore the echo or we'd persist the preview.
    if (!editing || dragRef.current || resizeRef.current || addCard) return;
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
    if (changed) onItemsChange(merged);
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
    if (!id || id === ADD_GHOST_ID) return;
    const it = itemsRef.current.find((i) => i.i === id);
    if (!it) return;
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
      const c = constraintsFor(it.module_id, it.widget_id);
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
        const w = Math.max(
          r.minW,
          Math.min(r.cols - r.x, r.startW + Math.round((ev.clientX - r.pointerX) / r.stepX)),
        );
        const h = Math.max(r.minH, r.startH + Math.round((ev.clientY - r.pointerY) / r.stepY));
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
          if (changed) onItemsChangeRef.current(final);
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

    const move = (ev: PointerEvent) => {
      const d = dragRef.current;
      if (!d) return;
      const left = d.startLeft + (ev.clientX - d.pointerX);
      const top = d.startTop + (ev.clientY - d.pointerY);
      const x = Math.max(0, Math.min(d.cols - d.w, Math.round((left - d.padX) / d.stepX)));
      const y = Math.max(0, Math.round((top - d.padY) / d.stepY));
      const nextLayout = reflowFree(d.snapshot, d.id, x, y);
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
        if (changed) onItemsChangeRef.current(final);
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
  const commitAdd = useCallback((card: CatalogCard, x: number, y: number) => {
    const newItem: DashboardItem = {
      i:
        typeof crypto !== "undefined" && crypto.randomUUID
          ? crypto.randomUUID()
          : `card-${Date.now()}-${Math.random().toString(36).slice(2)}`,
      module_id: card.module_id,
      widget_id: card.widget_id,
      title: card.title,
      x,
      y,
      w: card.default_w,
      h: card.default_h,
      config: configDefaults(card.config_schema),
    };
    onItemsChangeRef.current(reflowFree([...itemsRef.current, newItem], newItem.i, x, y));
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
      const width = gridEl?.clientWidth || rect.width;
      if (!width) return null;
      const [mx, my] = grid.margin;
      const colW = (width - mx * (cols - 1) - mx * 2) / cols;
      const x = Math.max(0, Math.min(cols - w, Math.round((clientX - rect.left - mx) / (colW + mx))));
      const y = Math.max(0, Math.round((clientY - rect.top - my) / (grid.rowHeight + my)));
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
    (card: CatalogCard, e: React.PointerEvent) => {
      if (!editing) return;
      const startX = e.clientX;
      const startY = e.clientY;
      setAddCard(card);
      setAddPointer({ x: startX, y: startY });
      setAddCell(cellWithinGrid(startX, startY, card.default_w));
      let moved = false;
      const move = (ev: PointerEvent) => {
        if (Math.abs(ev.clientX - startX) > 4 || Math.abs(ev.clientY - startY) > 4) moved = true;
        setAddPointer({ x: ev.clientX, y: ev.clientY });
        setAddCell(cellWithinGrid(ev.clientX, ev.clientY, card.default_w));
      };
      const finish = (ev: PointerEvent) => {
        cleanup();
        const cell = cellWithinGrid(ev.clientX, ev.clientY, card.default_w);
        if (cell) commitAdd(card, cell.x, cell.y);
        else if (!moved)
          commitAdd(card, 0, itemsRef.current.reduce((m, it) => Math.max(m, it.y + it.h), 0));
      };
      const cancel = () => cleanup();
      const cleanup = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", finish);
        window.removeEventListener("pointercancel", cancel);
        addTeardownRef.current = () => {};
        setAddCard(null);
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
    [editing, cellWithinGrid, commitAdd],
  );

  // Publish the starter so the palette (a sibling) can begin the gesture from its
  // own `pointerdown`, synchronously.
  useEffect(() => {
    startAddRef.current = startAdd;
    return () => {
      startAddRef.current = null;
    };
  }, [startAdd, startAddRef]);

  return (
    <div ref={wrapRef} className="min-h-full" onPointerDown={freeReflow ? onPointerDown : undefined}>
      <GridLayout
        className={cn("min-h-full", !mounted && "rgl-mounting")}
        layout={layout}
        cols={cols}
        rowHeight={grid.rowHeight}
        margin={grid.margin}
        isDraggable={editing && grid.compactType !== null}
        isResizable={editing && grid.compactType !== null}
        draggableHandle=".dashboard-drag-handle"
        onLayoutChange={handleLayoutChange}
        compactType={grid.compactType}
      >
        {items.map((it) => {
          const h = cardHandlers.get(it.i);
          return (
            <div
              key={it.i}
              data-grid-id={it.i}
              className={cn(
                "group",
                draggingId === it.i && "rgl-free-dragging",
                resizingId === it.i && "rgl-free-resizing",
              )}
            >
              {/* Inner wrapper carries the add/remove animation (transform/opacity)
                  so it never fights RGL's `transform` on the grid-item above. */}
              <div
                className={cn(
                  "h-full",
                  recentlyAddedId === it.i && "animate-in fade-in-0 zoom-in-95 duration-200",
                  removingIds.has(it.i) &&
                    "animate-out fade-out-0 zoom-out-95 pointer-events-none duration-150",
                )}
              >
                <DashboardCard
                  item={it}
                  logId={logId}
                  editing={editing}
                  schema={schemaFor(it.module_id, it.widget_id)}
                  chrome={settings.chrome}
                  onUpdate={h?.onUpdate ?? (() => {})}
                  onRemove={h?.onRemove ?? (() => {})}
                />
              </div>
              {freeReflow && constraintsFor(it.module_id, it.widget_id).resizable && (
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
            <div className="h-full w-full rounded-lg border-2 border-dashed border-primary/60 bg-primary/10" />
          </div>
        )}
      </GridLayout>
      {addCard &&
        addPointer &&
        createPortal(
          <div
            className="pointer-events-none fixed z-50 max-w-[16rem] truncate rounded-md border border-border bg-card px-2 py-1 text-xs font-medium shadow-lg animate-in fade-in-0 zoom-in-95 duration-150"
            style={{ left: addPointer.x + 12, top: addPointer.y + 12 }}
          >
            {addCard.title}
          </div>,
          document.body,
        )}
    </div>
  );
}
