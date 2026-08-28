"use client";

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
  Check,
  Download,
  Eye,
  LayoutDashboard,
  Loader2,
  Pencil,
  Redo2,
  Share2,
  Undo2,
} from "lucide-react";
import { toast } from "sonner";
import { AnimatePresence, motion, useReducedMotion, type Transition } from "framer-motion";

import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { EmptyState } from "@/components/empty-state";
import { CardPalette } from "@/components/dashboards/card-palette";
import { CardInspector } from "@/components/dashboards/card-inspector";
import { CanvasZoomControls } from "@/components/dashboards/canvas-zoom-controls";
import {
  DashboardCanvas,
  gridPixelHeight,
  type AddStarter,
} from "@/components/dashboards/dashboard-canvas";
import { NoTeamShareGate, ShareDialog } from "@/components/dashboards/share-dialog";
import { DashboardViewSkeleton } from "./dashboard-view-skeleton";
import {
  DashboardFilterProvider,
  DashboardWidgetScope,
  useDashboardFilter,
} from "@/components/dashboards/dashboard-filter";
import { DashboardFilterBar } from "@/components/dashboards/dashboard-filter-bar";
import { DashboardSettingsDialog } from "@/components/dashboards/dashboard-settings-dialog";
import { DashboardTimeRange } from "@/components/dashboards/dashboard-time-range";
import { useItemHistory } from "@/lib/dashboards/use-item-history";
import { useEventLogs } from "@/lib/queries";
import { useNoShareTargets } from "@/lib/sharing-queries";
import {
  canvasSettings,
  DEFAULT_CANVAS_SETTINGS,
  GRID,
  initialColumnFilters as loadColumnFilters,
  initialTimeFilters as loadTimeFilters,
  useCardCatalog,
  useDashboard,
  useEventColumns,
  useTimeBounds,
  useUpdateDashboard,
  type CanvasSettings,
  type DashboardItem,
} from "@/lib/dashboard-queries";
import type { FilterEntry } from "@/lib/api-types";

// Shared "subtle & snappy" timing for the view's enter/exit transitions.
const MOTION: Transition = { duration: 0.18, ease: [0.2, 0, 0, 1] };

// Canvas zoom bounds (n8n-style). Buttons step multiplicatively; ctrl/⌘+wheel
// and trackpad pinch zoom continuously at the cursor.
const MIN_ZOOM = 0.25;
const MAX_ZOOM = 2;
const ZOOM_STEP = 1.25;
/** The canvas scroll container's `p-3` padding, in px — part of the
 * scroll-anchor math when zooming around a point. */
const CANVAS_PAD = 12;

/** Autosave debounce: a burst of edits (drag, typing) coalesces into one PATCH. */
const AUTOSAVE_MS = 1000;

/** The exact PATCH body autosave persists. Also serialized for the dirty
 * check, so "needs saving" and "what gets saved" can never diverge. */
function boardPatch(s: {
  name: string;
  items: DashboardItem[];
  logId: string | null;
  settings: CanvasSettings;
}) {
  return {
    name: s.name.trim() || "Untitled",
    items: s.items,
    event_log_id: s.logId,
    settings: s.settings,
  };
}

export function DashboardView({ dashboardId }: { dashboardId: string }) {
  const { data: dashboard, isLoading, isError } = useDashboard(dashboardId);
  const { data: logs } = useEventLogs({ status: "ready" });
  // Shared with the palette and the canvas via react-query's cache; the
  // inspector needs it to resolve the selected card's views/kpis/schema/help.
  const { data: catalog } = useCardCatalog();
  const update = useUpdateDashboard(dashboardId);

  const [editing, setEditing] = useState(false);
  const [name, setName] = useState("");
  const [items, setItems] = useState<DashboardItem[]>([]);
  // Which cards are selected. Lives here rather than in the canvas because the
  // inspector and the toolbar's bulk actions are siblings of it.
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [logId, setLogId] = useState<string | null>(null);
  const [settings, setSettings] = useState<CanvasSettings>(DEFAULT_CANVAS_SETTINGS);
  // The canvas publishes its add-drag starter here; the palette calls it
  // synchronously from `pointerdown` so the gesture matches an in-canvas drag.
  const startAddRef = useRef<AddStarter | null>(null);
  // Undo/redo over the item list. Every edit autosaves, so without this a
  // mis-drag or a stray Delete is permanent.
  const history = useItemHistory(items, setItems);
  const [shareOpen, setShareOpen] = useState(false);
  // Shared enter/exit timing for the framer-motion bits; zeroed (instant) under
  // prefers-reduced-motion since width/height/opacity animations bypass the CSS
  // guard. The CSS/tw-animate animations are handled by that guard separately.
  const reduceMotion = useReducedMotion();
  const motionTransition: Transition = reduceMotion ? { duration: 0 } : MOTION;
  // Shared boards open read-only for the recipient – no edit toolbar, no log
  // picker. The backend also 404s owner-only mutations, so this is just UX.
  const isOwner = dashboard?.is_owner ?? true;
  // Sharing needs a team: without one the Share button is disabled (not
  // hidden) with a tooltip explaining why. Only fetched for owners – the
  // button doesn't exist on shared boards.
  const noTeam = useNoShareTargets(isOwner);

  // ── Autosave ─────────────────────────────────────────────────────────────
  // Edits persist automatically – there is no Save button. `savedRef` holds
  // the serialized last-persisted payload (the dirty check), `saveState`
  // drives the subtle Saving…/Saved toolbar indicator, and the refs keep the
  // flush paths (debounce timer, unmount, pagehide) reading current values.
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const savedRef = useRef<string>("");
  const hydratedRef = useRef(false);
  const inFlightRef = useRef(false);
  const stateRef = useRef({ name, items, logId, settings });
  stateRef.current = { name, items, logId, settings };
  const updateRef = useRef(update.mutateAsync);
  updateRef.current = update.mutateAsync;

  // ── Canvas zoom ──────────────────────────────────────────────────────────
  const [zoom, setZoom] = useState(1);
  const zoomRef = useRef(zoom);
  zoomRef.current = zoom;
  const scrollRef = useRef<HTMLDivElement>(null);
  // Scroll anchor for the next zoom commit: the content point under the cursor
  // (or the viewport center) must stay put across the scale change. Applied in
  // a layout effect so the corrected scrollTop lands before paint.
  const zoomAnchorRef = useRef<{ offsetY: number; contentY: number } | null>(null);
  const applyZoom = (next: number, clientY?: number) => {
    const z = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, next));
    const prev = zoomRef.current;
    if (z === prev) return;
    const el = scrollRef.current;
    if (el) {
      const rect = el.getBoundingClientRect();
      const offsetY = clientY != null ? clientY - rect.top : el.clientHeight / 2;
      zoomAnchorRef.current = {
        offsetY,
        contentY: (el.scrollTop + offsetY - CANVAS_PAD) / prev,
      };
    }
    setZoom(z);
  };
  const applyZoomRef = useRef(applyZoom);
  applyZoomRef.current = applyZoom;
  useLayoutEffect(() => {
    const a = zoomAnchorRef.current;
    const el = scrollRef.current;
    if (!a || !el) return;
    zoomAnchorRef.current = null;
    el.scrollTop = a.contentY * zoom + CANVAS_PAD - a.offsetY;
  }, [zoom]);
  // Ctrl/⌘+wheel zooms at the cursor (a trackpad pinch reports as ctrl+wheel).
  // Native non-passive listener: React registers `onWheel` passively, so only
  // this can preventDefault the browser's own page zoom. Re-attached once the
  // canvas actually mounts (the loading skeleton returns early).
  const canvasReady = !isLoading && !!dashboard;
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      const factor = Math.exp(-e.deltaY * (e.deltaMode === 1 ? 0.05 : 0.002));
      applyZoomRef.current(zoomRef.current * factor, e.clientY);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [canvasReady]);
  // Fit the whole board vertically (width always fits), capped at 100%.
  const fitZoom = () => {
    const el = scrollRef.current;
    if (!el) return;
    const h = gridPixelHeight(items, GRID);
    if (h <= 0) return;
    const z = Math.max(MIN_ZOOM, Math.min(1, (el.clientHeight - CANVAS_PAD * 2) / h));
    if (z === zoomRef.current) {
      el.scrollTop = 0;
      return;
    }
    zoomAnchorRef.current = { offsetY: CANVAS_PAD, contentY: 0 };
    setZoom(z);
  };

  // Hydrate local edit state once per board (the impl remounts per dashboardId
  // – keyed in dashboard-view.tsx). After hydration, local state is the source
  // of truth and flows one way (autosave → server); a background refetch (the
  // mutation's onSettled invalidate) must never clobber edits made while a
  // save was in flight.
  useEffect(() => {
    if (!dashboard || hydratedRef.current) return;
    hydratedRef.current = true;
    const s = canvasSettings(dashboard.settings);
    setName(dashboard.name);
    setItems(dashboard.items);
    setLogId(dashboard.event_log_id);
    setSettings(s);
    savedRef.current = JSON.stringify(
      boardPatch({
        name: dashboard.name,
        items: dashboard.items,
        logId: dashboard.event_log_id,
        settings: s,
      }),
    );
  }, [dashboard]);

  // One save at a time; if edits land while a PATCH is in flight, run once
  // more with the latest state when it settles. Ref-based (reassigned every
  // render) so the debounce timer and the unmount flush always call a closure
  // with current values.
  const runSaveRef = useRef<() => Promise<void>>(async () => {});
  runSaveRef.current = async () => {
    if (inFlightRef.current || !hydratedRef.current || !isOwner) return;
    const body = boardPatch(stateRef.current);
    const key = JSON.stringify(body);
    if (key === savedRef.current) return;
    inFlightRef.current = true;
    setSaveState("saving");
    let saved = false;
    try {
      await updateRef.current(body);
      savedRef.current = key;
      saved = true;
      setSaveState("saved");
    } catch {
      // No auto-retry loop: the next edit (or the unmount flush) retries.
      setSaveState("error");
      toast.error("Could not save dashboard");
    } finally {
      inFlightRef.current = false;
    }
    if (saved && JSON.stringify(boardPatch(stateRef.current)) !== savedRef.current) {
      void runSaveRef.current();
    }
  };

  // Debounced autosave: any change to the persisted payload (re)arms the
  // timer, so a drag burst or typing in the name coalesces into one PATCH.
  useEffect(() => {
    if (!isOwner || !hydratedRef.current) return;
    if (JSON.stringify(boardPatch({ name, items, logId, settings })) === savedRef.current) return;
    const t = setTimeout(() => void runSaveRef.current(), AUTOSAVE_MS);
    return () => clearTimeout(t);
  }, [name, items, logId, settings, isOwner]);

  // Flush the pending change when the view unmounts (navigating away, or
  // switching boards – the impl is keyed by dashboardId) so nothing is lost
  // to the debounce window. The mutation and its cache callbacks still run
  // after unmount; React Query does not cancel mutations.
  useEffect(() => {
    return () => {
      void runSaveRef.current();
    };
  }, []);

  // Best-effort flush on hard unload (tab close / refresh): `keepalive` lets
  // the PATCH outlive the document. SPA navigation is the unmount flush above.
  // Bypasses the mutation on purpose – no cache work is meaningful here. Does
  // not update savedRef: if the page survives (bfcache), the normal path
  // re-sends an identical, idempotent full payload at worst.
  useEffect(() => {
    const flush = () => {
      if (!hydratedRef.current) return;
      const body = boardPatch(stateRef.current);
      if (JSON.stringify(body) === savedRef.current) return;
      void api(`/api/v1/dashboards/${dashboardId}`, {
        method: "PATCH",
        json: body,
        keepalive: true,
      }).catch(() => undefined);
    };
    window.addEventListener("pagehide", flush);
    return () => window.removeEventListener("pagehide", flush);
  }, [dashboardId]);

  // The board loads with its committed view applied (view mode): the owner's
  // last column-filter bar + time-range window (falling back to the active
  // saved preset for legacy boards). Read from the *saved* settings so it's
  // stable for the filter provider's mount – and it's the same settings a share
  // recipient receives, so their board opens on the owner's filtered view.
  const initialFilters = useMemo(
    () => (dashboard ? loadColumnFilters(canvasSettings(dashboard.settings)) : []),
    [dashboard],
  );
  const initialTime = useMemo(
    () => (dashboard ? loadTimeFilters(canvasSettings(dashboard.settings)) : []),
    [dashboard],
  );

  // Persist the committed filter view (column bar + time range) onto the board's
  // settings so it transfers to share recipients. Owner-only – it folds into
  // `settings`, which rides the normal autosave path. Functional update so a
  // concurrent settings edit (a preset toggle, granularity change) isn't lost.
  const handleFilterCommit = (next: {
    columnFilters: FilterEntry[];
    timeFilters: FilterEntry[];
  }) => {
    setSettings((s) => ({
      ...s,
      column_filters: next.columnFilters,
      time_filters: next.timeFilters,
    }));
  };

  // The column count is fixed now, so settings edits never move a card and this
  // is a plain setter. (It used to rescale every card's x/w whenever the board's
  // granularity changed the column count.)
  const changeSettings = (next: CanvasSettings) => setSettings(next);

  // Only logs of the board's own model are bindable – a case-centric board can
  // only render case-centric logs and vice-versa.
  const readyLogs = useMemo(
    () =>
      (logs ?? []).filter(
        (l) => l.status === "ready" && l.log_model === dashboard?.log_model,
      ),
    [logs, dashboard?.log_model],
  );

  const exportJson = () => {
    const doc = {
      kind: "mate.dashboard",
      version: 1,
      name,
      description: dashboard?.description ?? null,
      log_model: dashboard?.log_model ?? "case_centric",
      items,
      settings,
    };
    const blob = new Blob([JSON.stringify(doc, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${(name || "dashboard").replace(/[^\w.-]+/g, "-").toLowerCase()}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  if (isLoading) {
    return <DashboardViewSkeleton />;
  }
  if (isError || !dashboard) {
    return (
      <EmptyState
        icon={LayoutDashboard}
        title="Dashboard not found"
        description="It may have been deleted."
        primaryAction={
          <Button asChild variant="outline">
            <Link href="/dashboards">Back to dashboards</Link>
          </Button>
        }
      />
    );
  }

  return (
    // The filter provider wraps the whole view (toolbar included) so the
    // settings dialog can read/apply the live filter state for saved filters.
    // It mounts seeded with the active saved filter's filters.
    <DashboardFilterProvider
      initialColumnFilters={initialFilters}
      initialTimeFilters={initialTime}
      onCommit={isOwner ? handleFilterCommit : undefined}
    >
      <div className="flex h-full flex-col">
      {/* Toolbar */}
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border px-4 py-2.5 sm:px-6 lg:px-8">
        {/* View-mode name lives in the global topbar breadcrumb now; only the
            rename field remains, and only while editing. */}
        {editing && (
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="h-8 w-56 text-sm font-medium"
            placeholder="Dashboard name"
            aria-label="Dashboard name"
          />
        )}

        {isOwner && (
          <div className="ml-2 flex items-center gap-1.5">
            <span className="text-xs text-muted-foreground">Log</span>
            <Select
              value={logId ?? "__none__"}
              onValueChange={(v) => setLogId(v === "__none__" ? null : v)}
            >
              <SelectTrigger className="h-8 w-48 text-xs">
                <SelectValue placeholder="Select event log" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__none__">No log selected</SelectItem>
                {readyLogs.map((l) => (
                  <SelectItem key={l.id} value={l.id}>
                    {l.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}

        <div className="ml-auto flex items-center gap-2">
          {/* Subtle autosave status. Always-mounted live region (content swaps)
              so screen readers announce transitions; empty while idle. */}
          {isOwner && (
            <span
              aria-live="polite"
              className="flex items-center gap-1.5 text-xs text-muted-foreground"
            >
              {saveState === "saving" ? (
                <>
                  <Loader2 className="h-3 w-3 animate-spin" />
                  Saving…
                </>
              ) : saveState === "saved" ? (
                <>
                  <Check className="h-3 w-3" />
                  Saved
                </>
              ) : saveState === "error" ? (
                <span className="text-destructive">Save failed</span>
              ) : null}
            </span>
          )}
          {editing && (
            <span className="flex items-center gap-1">
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="h-8 w-8"
                aria-label="Undo"
                title="Undo (⌘Z)"
                disabled={!history.canUndo}
                onClick={history.undo}
              >
                <Undo2 className="h-3.5 w-3.5" />
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="h-8 w-8"
                aria-label="Redo"
                title="Redo (⌘⇧Z)"
                disabled={!history.canRedo}
                onClick={history.redo}
              >
                <Redo2 className="h-3.5 w-3.5" />
              </Button>
            </span>
          )}
          {editing && (
            <DashboardSettingsDialog settings={settings} onChange={changeSettings} />
          )}
          {editing && (
            <Button type="button" variant="outline" size="sm" onClick={exportJson}>
              <Download className="mr-1.5 h-3.5 w-3.5" />
              Export
            </Button>
          )}
          {editing ? (
            // Binary mode toggle: exactly one View (editing) / one Edit (viewing)
            // button. Changes autosave, so leaving edit mode needs no save step.
            <Button
              type="button"
              size="sm"
              onClick={() => {
                // A selection is an edit-mode concept; leaving with cards still
                // highlighted would show a ring nothing can act on.
                setSelectedIds([]);
                setEditing(false);
              }}
            >
              <Eye className="mr-1.5 h-3.5 w-3.5" />
              View
            </Button>
          ) : isOwner ? (
            <>
              <NoTeamShareGate noTeam={noTeam}>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={noTeam}
                  aria-disabled={noTeam || undefined}
                  onClick={() => setShareOpen(true)}
                >
                  <Share2 className="mr-1.5 h-3.5 w-3.5" />
                  Share
                </Button>
              </NoTeamShareGate>
              <Button type="button" size="sm" onClick={() => setEditing(true)}>
                <Pencil className="mr-1.5 h-3.5 w-3.5" />
                Edit
              </Button>
            </>
          ) : (
            <Badge variant="outline" className="shrink-0 font-normal text-muted-foreground">
              <Eye className="mr-1.5 h-3.5 w-3.5" />
              Shared · read-only
            </Badge>
          )}
        </div>
      </div>

      {/* Body: global filter bar + (palette + canvas) + time range. The filter
          provider (above) scopes every widget's queries so a filter change
          skeletons and refetches them all without touching the rest of the app. */}
        <div className="flex min-h-0 flex-1 flex-col">
          <AnimatePresence initial={false}>
            {logId && (
              <motion.div
                key="filter-bar"
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: "auto", opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={motionTransition}
                className="overflow-hidden"
              >
                <DashboardFilterBarConnected logId={logId} />
              </motion.div>
            )}
          </AnimatePresence>
          <div className="flex min-h-0 flex-1">
            <DashboardWidgetScope>
              {/* The zoom controls float over the scroll container (not inside
                  it) so they stay put while the board scrolls. */}
              <div className="relative min-h-0 flex-1">
                {/* The palette OVERLAYS the canvas rather than sitting beside
                    it. As a flex sibling animating 0 -> 16rem it changed the
                    grid's measured width on every frame, so entering edit mode
                    re-laid-out and visibly moved every card. As an overlay the
                    grid keeps its width; only the scroll container's padding
                    changes, once, on the mode toggle. */}
                <AnimatePresence initial={false}>
                  {editing && (
                    <motion.div
                      key="palette"
                      initial={{ x: "-100%", opacity: 0 }}
                      animate={{ x: 0, opacity: 1 }}
                      exit={{ x: "-100%", opacity: 0 }}
                      transition={motionTransition}
                      className="absolute inset-y-0 left-0 z-20 w-64"
                    >
                      <CardPalette
                        onStartAdd={(req, e) => startAddRef.current?.(req, e)}
                        logModel={dashboard.log_model}
                      />
                    </motion.div>
                  )}
                </AnimatePresence>
                {/* The inspector mirrors the palette: an overlay on the right,
                    for the same reason. As a flex sibling it would re-measure
                    the grid every time a card was selected — reproducing the
                    card-jumping bug on the other side. */}
                <AnimatePresence initial={false}>
                  {editing && (
                    <motion.div
                      key="inspector"
                      initial={{ x: "100%", opacity: 0 }}
                      animate={{ x: 0, opacity: 1 }}
                      exit={{ x: "100%", opacity: 0 }}
                      transition={motionTransition}
                      className="absolute inset-y-0 right-0 z-20"
                    >
                      <CardInspector
                        items={items}
                        selectedIds={selectedIds}
                        catalog={catalog}
                        logId={logId}
                        onUpdate={(id, patch) =>
                          history.commit(
                            items.map((it) => (it.i === id ? { ...it, ...patch } : it)),
                            "config",
                          )
                        }
                        onRemove={(ids) => {
                          const drop = new Set(ids);
                          history.commit(
                            items.filter((it) => !drop.has(it.i)),
                            "remove",
                          );
                          setSelectedIds([]);
                        }}
                        onClose={() => setSelectedIds([])}
                      />
                    </motion.div>
                  )}
                </AnimatePresence>
                <div
                  ref={scrollRef}
                  className={cn(
                    "dashboard-canvas-bg relative h-full overflow-auto p-3",
                    // Static, not animated: the grid must re-measure at most
                    // once per mode change.
                    editing && "pl-[16.75rem] pr-[18.75rem]",
                  )}
                >
                  {items.length === 0 && !editing ? (
                    <EmptyState
                      icon={LayoutDashboard}
                      title="No cards yet"
                      description={
                        isOwner
                          ? "Switch to edit mode to add cards from your modules."
                          : "The owner hasn't added any cards yet."
                      }
                      primaryAction={
                        isOwner ? (
                          <Button size="sm" onClick={() => setEditing(true)}>
                            <Pencil className="mr-1.5 h-3.5 w-3.5" />
                            Edit dashboard
                          </Button>
                        ) : undefined
                      }
                    />
                  ) : (
                    // In edit mode the canvas is always mounted – even empty – so
                    // it stays a react-grid-layout drop target for the palette.
                    <DashboardCanvas
                      items={items}
                      logId={logId}
                      editing={editing}
                      startAddRef={startAddRef}
                      settings={settings}
                      zoom={zoom}
                      selectedIds={selectedIds}
                      onSelectionChange={setSelectedIds}
                      historyApplying={history.isApplying}
                      // Every geometry change goes through the history so it
                      // can be undone; the label groups a burst of the same
                      // edit into one step.
                      onItemsChange={(next, label) => history.commit(next, label ?? "edit")}
                    />
                  )}
                  <AnimatePresence>
                    {items.length === 0 && editing && (
                      <motion.div
                        key="empty-hint"
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        transition={motionTransition}
                        className="pointer-events-none absolute inset-0 flex items-center justify-center p-6"
                      >
                        <div className="rounded-lg border border-dashed border-border bg-background/70 px-6 py-4 text-center backdrop-blur-sm">
                          <p className="text-sm font-medium">Empty board</p>
                          <p className="mt-1 text-xs text-muted-foreground">
                            Drag a card from the left onto the canvas, or click one to add it.
                          </p>
                        </div>
                      </motion.div>
                    )}
                  </AnimatePresence>
                  </div>
                {(items.length > 0 || editing) && (
                  <CanvasZoomControls
                    zoom={zoom}
                    min={MIN_ZOOM}
                    max={MAX_ZOOM}
                    onZoomIn={() => applyZoom(zoomRef.current * ZOOM_STEP)}
                    onZoomOut={() => applyZoom(zoomRef.current / ZOOM_STEP)}
                    onReset={() => applyZoom(1)}
                    onFit={fitZoom}
                  />
                )}
              </div>
            </DashboardWidgetScope>
          </div>
          <AnimatePresence initial={false}>
            {logId && (
              <motion.div
                key="time-range"
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: "auto", opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={motionTransition}
                className="overflow-hidden"
              >
                <DashboardTimeRangeConnected logId={logId} />
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
      {isOwner && (
        <ShareDialog
          dashboardId={dashboardId}
          dashboardName={name}
          open={shareOpen}
          onOpenChange={setShareOpen}
        />
      )}
    </DashboardFilterProvider>
  );
}

/** Binds the column-filter bar to the bound log's columns + the dashboard's
 * ephemeral filter state. Its own data (column specs) is fetched on the app's
 * QueryClient, so a filter commit doesn't churn it. */
function DashboardFilterBarConnected({ logId }: { logId: string }) {
  const { columnFilters, setColumnFilters } = useDashboardFilter();
  const { data: columns } = useEventColumns(logId);
  if (!columns || columns.length === 0) return null;
  return (
    <DashboardFilterBar
      logId={logId}
      columns={columns}
      filters={columnFilters}
      onChange={setColumnFilters}
    />
  );
}

function DashboardTimeRangeConnected({ logId }: { logId: string }) {
  const { timeFilters, setTimeFilters } = useDashboardFilter();
  const { data: bounds } = useTimeBounds(logId);
  return (
    <DashboardTimeRange bounds={bounds} committed={timeFilters} onChange={setTimeFilters} />
  );
}
