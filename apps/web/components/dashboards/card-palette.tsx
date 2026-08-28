"use client";

import { useMemo, useState } from "react";
import { Boxes, ChevronDown, Search } from "lucide-react";

import { cn } from "@/lib/cn";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cardIcon } from "@/components/dashboards/card-icon";
import type { AddRequest } from "@/components/dashboards/dashboard-canvas";
import {
  useCardCatalog,
  useDatasetCatalog,
  type DashboardCard,
  type DatasetCatalogEntry,
  type LogModel,
} from "@/lib/dashboard-queries";

const MODEL_LABELS: Record<LogModel, string> = {
  case_centric: "case-centric",
  object_centric: "object-centric (OCEL)",
};

/** A palette row: a module-authored widget card, or a module dataset rendered
 * through the generic-viz layer. `applicable` is false when the entry's
 * `log_models` don't include the board's model – it is then shown grayed-out
 * (not hidden) with a tooltip explaining why it can't be placed. */
type Entry = (
  | { type: "widget"; key: string; title: string; description: string | null; icon: string | null; card: DashboardCard }
  | {
      type: "viz";
      key: string;
      title: string;
      description: string | null;
      icon: string | null;
      shape: DatasetCatalogEntry["shape"];
      dataset: DatasetCatalogEntry;
    }
) & { applicable: boolean; logModels: LogModel[] };

/**
 * Left-rail palette of everything the user's installed modules expose - both
 * widget *cards* and data *datasets* (rendered via the platform's generic
 * visualizations) - grouped per module into collapsible sections. Pressing a
 * row starts a pointer-drag onto the canvas (`onStartAdd`); the canvas tracks
 * the cursor and drops it. A press without dragging adds it at the bottom.
 */
export function CardPalette({
  onStartAdd,
  logModel,
}: {
  /** Begin a palette→canvas add at the pointer's position. */
  onStartAdd: (req: AddRequest, e: React.PointerEvent) => void;
  /** Only entries whose `log_models` include the board's model are shown. */
  logModel: LogModel;
}) {
  const { data: cards, isLoading: cardsLoading } = useCardCatalog();
  const { data: datasets, isLoading: datasetsLoading } = useDatasetCatalog();
  const isLoading = cardsLoading || datasetsLoading;
  const [query, setQuery] = useState("");
  // Module ids the user has explicitly collapsed. A search overrides this so
  // matches are never hidden behind a collapsed header.
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const matches = (title: string, moduleName: string, description: string | null) =>
      !q ||
      title.toLowerCase().includes(q) ||
      moduleName.toLowerCase().includes(q) ||
      (description ?? "").toLowerCase().includes(q);

    const byModule = new Map<string, { id: string; name: string; entries: Entry[] }>();
    const ensure = (id: string, name: string) => {
      const g = byModule.get(id) ?? { id, name, entries: [] };
      byModule.set(id, g);
      return g;
    };

    for (const c of cards ?? []) {
      if (!matches(c.title, c.module_name, c.description)) continue;
      ensure(c.module_id, c.module_name).entries.push({
        type: "widget",
        key: `w:${c.module_id}:${c.widget_id}`,
        title: c.title,
        description: c.description,
        icon: c.icon,
        card: c,
        applicable: c.log_models.includes(logModel),
        logModels: c.log_models,
      });
    }
    for (const d of datasets ?? []) {
      if (!matches(d.title, d.module_name, d.description)) continue;
      ensure(d.module_id, d.module_name).entries.push({
        type: "viz",
        key: `d:${d.module_id}:${d.dataset_id}`,
        title: d.title,
        description: d.description,
        icon: d.icon,
        shape: d.shape,
        dataset: d,
        applicable: d.log_models.includes(logModel),
        logModels: d.log_models,
      });
    }
    // Placeable entries first inside each module group; the sort is stable, so
    // the catalog's declared order is kept within each half.
    for (const g of byModule.values()) {
      g.entries.sort((a, b) => Number(b.applicable) - Number(a.applicable));
    }
    return [...byModule.values()].sort((a, b) => a.name.localeCompare(b.name));
  }, [cards, datasets, query, logModel]);

  const searching = query.trim().length > 0;
  const toggle = (id: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const requestFor = (entry: Entry): AddRequest =>
    entry.type === "widget"
      ? { kind: "widget", card: entry.card }
      : { kind: "viz", dataset: entry.dataset };

  return (
    <div className="flex h-full w-64 shrink-0 flex-col border-r border-border bg-muted/20">
      <div className="border-b border-border p-3">
        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Cards &amp; data
        </p>
        <p className="mt-0.5 text-[11px] text-muted-foreground">Drag onto the board to add.</p>
        <div className="relative mt-2">
          <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search cards & data…"
            className="h-8 pl-7 text-xs"
          />
        </div>
      </div>

      <ScrollArea className="min-h-0 flex-1 [&_[data-slot=scroll-area-viewport]>div]:!block">
        <div className="space-y-3 p-3">
          {isLoading && <p className="text-xs text-muted-foreground">Loading…</p>}
          {!isLoading && groups.length === 0 && (
            <p className="text-xs text-muted-foreground">
              No matches. Modules expose cards and datasets via their manifest.
            </p>
          )}
          {groups.map((group) => {
            const open = searching || !collapsed.has(group.id);
            return (
              <section
                key={group.id}
                className="overflow-hidden rounded-lg border border-white/10 [border-top-color:var(--glass-refraction-top)] bg-card/40 backdrop-blur-md"
              >
                <button
                  type="button"
                  onClick={() => toggle(group.id)}
                  aria-expanded={open}
                  className="flex w-full items-center gap-2 bg-muted/40 px-2.5 py-2 text-left hover:bg-muted/60"
                >
                  <Boxes className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  <span className="min-w-0 flex-1 truncate text-xs font-semibold tracking-tight">
                    {group.name}
                  </span>
                  <span className="shrink-0 rounded-full bg-background/80 px-1.5 text-[10px] font-medium tabular-nums text-muted-foreground">
                    {group.entries.length}
                  </span>
                  <ChevronDown
                    className={cn(
                      "h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform",
                      !open && "-rotate-90",
                    )}
                  />
                </button>

                {open && (
                  <div className="space-y-1 p-1.5">
                    {group.entries.map((entry) => {
                      const Icon = cardIcon(entry.icon);
                      const row = (
                        <div
                          key={entry.key}
                          // Pointer-drag (not HTML5 DnD, which the prod proxy
                          // strips): press to start, the canvas tracks the cursor
                          // and drops. `touch-none` stops the scroll-area from
                          // hijacking the gesture on touch. Inapplicable entries
                          // (wrong log model for this board) don't start a drag.
                          onPointerDown={
                            entry.applicable
                              ? (e) => {
                                  if (e.button !== 0) return;
                                  e.preventDefault();
                                  onStartAdd(requestFor(entry), e);
                                }
                              : undefined
                          }
                          aria-disabled={!entry.applicable || undefined}
                          className={cn(
                            "group flex w-full touch-none items-start gap-2 rounded-md border border-transparent px-2 py-1.5 text-left",
                            entry.applicable
                              ? "cursor-grab transition-all duration-150 hover:border-border hover:bg-card active:scale-[0.98] active:cursor-grabbing"
                              : "cursor-not-allowed opacity-50",
                          )}
                        >
                          <Icon className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                          <span className="min-w-0 flex-1">
                            <span className="flex items-center gap-1.5">
                              <span className="min-w-0 flex-1 truncate text-xs font-medium">
                                {entry.title}
                              </span>
                              {entry.type === "viz" && (
                                <span className="shrink-0 rounded bg-muted px-1 text-[9px] font-medium uppercase tracking-wide text-muted-foreground">
                                  {entry.shape}
                                </span>
                              )}
                            </span>
                            {entry.description && (
                              <span className="mt-0.5 line-clamp-2 block text-[11px] leading-snug text-muted-foreground">
                                {entry.description}
                              </span>
                            )}
                          </span>
                        </div>
                      );
                      if (entry.applicable) return row;
                      const needs = entry.logModels.map((m) => MODEL_LABELS[m]).join(" or ");
                      return (
                        <Tooltip key={entry.key}>
                          <TooltipTrigger asChild>{row}</TooltipTrigger>
                          <TooltipContent side="right" className="max-w-xs">
                            Needs a {needs} log — this dashboard is bound to a{" "}
                            {MODEL_LABELS[logModel]} log.
                          </TooltipContent>
                        </Tooltip>
                      );
                    })}
                  </div>
                )}
              </section>
            );
          })}
        </div>
      </ScrollArea>
    </div>
  );
}
