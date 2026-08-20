"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  Check,
  Download,
  Eye,
  LayoutDashboard,
  Loader2,
  Pencil,
  Share2,
} from "lucide-react";
import { toast } from "sonner";
import { AnimatePresence, motion, useReducedMotion, type Transition } from "framer-motion";

import { cn } from "@/lib/cn";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { CardGridSkeleton } from "@/components/skeletons";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { EmptyState } from "@/components/empty-state";
import { CardPalette } from "@/components/dashboards/card-palette";
import { DashboardCanvas, type AddStarter } from "@/components/dashboards/dashboard-canvas";
import { ShareDialog } from "@/components/dashboards/share-dialog";
import {
  DashboardFilterProvider,
  DashboardWidgetScope,
  useDashboardFilter,
} from "@/components/dashboards/dashboard-filter";
import { DashboardFilterBar } from "@/components/dashboards/dashboard-filter-bar";
import { DashboardSettingsDialog } from "@/components/dashboards/dashboard-settings-dialog";
import { DashboardTimeRange } from "@/components/dashboards/dashboard-time-range";
import { useEventLogs } from "@/lib/queries";
import {
  activePresetFilters,
  canvasSettings,
  DEFAULT_CANVAS_SETTINGS,
  GRANULARITY,
  rescaleColumns,
  useDashboard,
  useEventColumns,
  useTimeBounds,
  useUpdateDashboard,
  type CanvasSettings,
  type DashboardItem,
} from "@/lib/dashboard-queries";

const DEFAULT_COLS = 12;

// Shared "subtle & snappy" timing for the view's enter/exit transitions.
const MOTION: Transition = { duration: 0.18, ease: [0.2, 0, 0, 1] };

export function DashboardView({ dashboardId }: { dashboardId: string }) {
  const { data: dashboard, isLoading, isError } = useDashboard(dashboardId);
  const { data: logs } = useEventLogs({ status: "ready" });
  const update = useUpdateDashboard(dashboardId);

  const [editing, setEditing] = useState(false);
  const [name, setName] = useState("");
  const [items, setItems] = useState<DashboardItem[]>([]);
  const [logId, setLogId] = useState<string | null>(null);
  const [settings, setSettings] = useState<CanvasSettings>(DEFAULT_CANVAS_SETTINGS);
  // The canvas publishes its add-drag starter here; the palette calls it
  // synchronously from `pointerdown` so the gesture matches an in-canvas drag.
  const startAddRef = useRef<AddStarter | null>(null);
  const [shareOpen, setShareOpen] = useState(false);
  // Shared enter/exit timing for the framer-motion bits; zeroed (instant) under
  // prefers-reduced-motion since width/height/opacity animations bypass the CSS
  // guard. The CSS/tw-animate animations are handled by that guard separately.
  const reduceMotion = useReducedMotion();
  const motionTransition: Transition = reduceMotion ? { duration: 0 } : MOTION;
  // Shared boards open read-only for the recipient – no edit toolbar, no log
  // picker. The backend also 404s owner-only mutations, so this is just UX.
  const isOwner = dashboard?.is_owner ?? true;
  // Snapshot of the last-saved state, to compute the dirty flag.
  const savedRef = useRef<string>("");

  // Hydrate local edit state once the dashboard loads (and after each save).
  useEffect(() => {
    if (!dashboard) return;
    setName(dashboard.name);
    setItems(dashboard.items);
    setLogId(dashboard.event_log_id);
    setSettings(canvasSettings(dashboard.settings));
    savedRef.current = JSON.stringify({
      name: dashboard.name,
      items: dashboard.items,
      event_log_id: dashboard.event_log_id,
      settings: canvasSettings(dashboard.settings),
    });
  }, [dashboard]);

  const dirty = useMemo(
    () =>
      savedRef.current !==
      JSON.stringify({ name, items, event_log_id: logId, settings }),
    [name, items, logId, settings],
  );

  // The board loads with its active saved filter applied (view mode). Read
  // from the *saved* settings so it's stable for the filter provider's mount.
  const initialFilters = useMemo(
    () => (dashboard ? activePresetFilters(canvasSettings(dashboard.settings)) : []),
    [dashboard],
  );

  // Settings edits flow through here so a granularity change (which changes the
  // column count) can rescale the cards' x/w to keep their relative layout.
  const changeSettings = (next: CanvasSettings) => {
    const fromCols = GRANULARITY[settings.granularity]?.cols ?? DEFAULT_COLS;
    const toCols = GRANULARITY[next.granularity]?.cols ?? DEFAULT_COLS;
    if (toCols !== fromCols) {
      setItems((prev) => rescaleColumns(prev, fromCols, toCols));
    }
    setSettings(next);
  };

  // Only logs of the board's own model are bindable – a case-centric board can
  // only render case-centric logs and vice-versa.
  const readyLogs = useMemo(
    () =>
      (logs ?? []).filter(
        (l) => l.status === "ready" && l.log_model === dashboard?.log_model,
      ),
    [logs, dashboard?.log_model],
  );

  const save = async () => {
    try {
      await update.mutateAsync({
        name: name.trim() || "Untitled",
        items,
        event_log_id: logId,
        settings,
      });
      savedRef.current = JSON.stringify({ name, items, event_log_id: logId, settings });
      toast.success("Dashboard saved");
    } catch {
      toast.error("Could not save dashboard");
    }
  };

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
    return (
      <div className="flex h-full flex-col">
        <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border px-4 py-2.5 sm:px-6 lg:px-8">
          <Skeleton className="h-8 w-8 rounded-md" />
          <Skeleton className="h-6 w-48" />
          <Skeleton className="ml-auto h-8 w-24 rounded-md" />
          <Skeleton className="h-8 w-20 rounded-md" />
        </div>
        <div className="flex-1 overflow-auto px-4 py-6 sm:px-6 lg:px-8">
          <CardGridSkeleton count={6} />
        </div>
      </div>
    );
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
    <DashboardFilterProvider initialColumnFilters={initialFilters}>
      <div className="flex h-full flex-col">
      {/* Toolbar */}
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border px-4 py-2.5 sm:px-6 lg:px-8">
        <Button asChild variant="ghost" size="icon" className="h-8 w-8" aria-label="Back">
          <Link href="/dashboards">
            <ArrowLeft className="h-4 w-4" />
          </Link>
        </Button>

        {editing ? (
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="h-8 w-56 text-sm font-medium"
            placeholder="Dashboard name"
            aria-label="Dashboard name"
          />
        ) : (
          <h1 className="truncate text-sm font-semibold tracking-tight">{name}</h1>
        )}

        <Badge variant="secondary" className="shrink-0 font-normal">
          {dashboard.log_model === "object_centric" ? "Object-centric" : "Case-centric"}
        </Badge>

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
            <>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => setEditing(false)}
              >
                <Eye className="mr-1.5 h-3.5 w-3.5" />
                View
              </Button>
              <Button
                type="button"
                size="sm"
                onClick={save}
                disabled={!dirty || update.isPending}
              >
                {update.isPending ? (
                  <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Check className="mr-1.5 h-3.5 w-3.5" />
                )}
                Save
              </Button>
            </>
          ) : isOwner ? (
            <>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setShareOpen(true)}
              >
                <Share2 className="mr-1.5 h-3.5 w-3.5" />
                Share
              </Button>
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
            <AnimatePresence initial={false}>
              {editing && (
                <motion.div
                  key="palette"
                  initial={{ width: 0, opacity: 0 }}
                  animate={{ width: "16rem", opacity: 1 }}
                  exit={{ width: 0, opacity: 0 }}
                  transition={motionTransition}
                  className="shrink-0 overflow-hidden"
                >
                  <CardPalette
                    onStartAdd={(card, e) => startAddRef.current?.(card, e)}
                    logModel={dashboard.log_model}
                  />
                </motion.div>
              )}
            </AnimatePresence>
            <DashboardWidgetScope>
              <div
                data-editing={editing}
                className="dashboard-canvas-bg relative min-h-0 flex-1 overflow-auto p-3"
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
                    onItemsChange={setItems}
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
  const { setTimeFilters } = useDashboardFilter();
  const { data: bounds } = useTimeBounds(logId);
  return <DashboardTimeRange bounds={bounds} onChange={setTimeFilters} />;
}
