"use client";

import { memo } from "react";
import Link from "next/link";
import { ArrowUpRight, GripVertical, Info, Settings2, X } from "lucide-react";

import { cn } from "@/lib/cn";
import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverHeader,
  PopoverTitle,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useWidget } from "@/lib/module-widgets";
import { CardConfigForm } from "@/components/dashboards/card-config-form";
import { configWithoutWidgetFilter } from "@/components/dashboards/widget-filter";
import { CardVizScope, configWithoutRender } from "@/components/dashboards/card-viz-scope";
import { GenericVizBody, VizSettings } from "@/components/dashboards/generic-viz-card";
import { WidgetHelpBody, hasHelp } from "@/components/dashboards/kit/help";
import { useDashboardFilterOptional } from "@/components/dashboards/dashboard-filter";
import type { DrillHandler } from "@/lib/dashboards/drill";
import {
  DEFAULT_CARD_CHROME,
  type CardChrome,
  type DashboardItem,
  type WidgetConfigSchema,
  type WidgetHelp,
} from "@/lib/dashboard-queries";

/** Patch the card settings emit back to the canvas. Covers both kinds: a widget
 * card only ever sets `title`/`config`; a viz card also sets `viz_id`/`mapping`. */
export interface CardPatch {
  title?: string | null;
  config?: Record<string, unknown>;
  viz_id?: string | null;
  mapping?: Record<string, unknown>;
  /** Geometry, set from the inspector's Layout section. Typing an exact size
   * is the only way to make two cards match precisely — dragging can't. */
  x?: number;
  y?: number;
  w?: number;
  h?: number;
}

/**
 * One placed card on the dashboard grid. Dispatches on `item.kind`:
 *   - "widget" (default/legacy): a module-authored bundle via `useWidget`.
 *   - "viz": a generic visualization bound to a module dataset.
 * The header chrome (drag handle, settings popover, remove) is shared; only the
 * body and the settings form differ.
 */
export const DashboardCard = memo(function DashboardCard({
  item,
  logId,
  editing,
  schema,
  chrome = DEFAULT_CARD_CHROME,
  description,
  help,
  drillHref,
  drillLabel = "Open in module",
  onDrill,
  onUpdate,
  onRemove,
}: {
  item: DashboardItem;
  logId: string | null;
  editing: boolean;
  schema: WidgetConfigSchema | null | undefined;
  chrome?: CardChrome;
  /** The widget/dataset's manifest description, surfaced via the header ⓘ.
   * Resolved from the catalog by the canvas (a placed item doesn't store it). */
  description?: string | null;
  /** Structured `help:` from the manifest. Preferred over `description`, which
   * is only the one-line palette blurb. */
  help?: WidgetHelp | null;
  /** Where "open in module" goes. `null` ⇒ nowhere (no bound log, drilling
   * disabled, or a viz card) and the button renders disabled. */
  drillHref?: string | null;
  drillLabel?: string;
  /** Handed to the widget so a clicked mark can navigate. */
  onDrill?: DrillHandler;
  onUpdate: (patch: CardPatch) => void;
  onRemove: () => void;
}) {
  const isViz = item.kind === "viz";
  const title = item.title || (isViz ? "Visualization" : item.widget_id) || "Card";
  const showHelp = hasHelp(help, description);
  // `Optional` because a card also renders in the palette preview and in tests,
  // outside a filter provider.
  const refetching = useDashboardFilterOptional()?.isRefetching ?? false;
  // A viz card renders a platform visualization of a dataset, so there is no
  // module view behind it to open — the canvas passes a null href for those.
  const canDrill = !isViz;
  // Don't let a drag begin when the user interacts with header controls.
  const stopDrag = (e: React.MouseEvent | React.PointerEvent) => e.stopPropagation();

  return (
    <div
      className={cn(
        "dashboard-card-root flex h-full flex-col overflow-hidden rounded-lg bg-card/80 shadow-sm supports-[backdrop-filter]:bg-card/70",
        "transition-[box-shadow,transform,outline-color] duration-150 ease-out",
        chrome.border && "border border-white/10 [border-top-color:var(--glass-refraction-top)]",
        // Edit mode: cards read as grabbable objects — a faint ring at rest
        // that firms up (with a soft lift) under the pointer, n8n-node style.
        editing && "ring-1 ring-border/60 hover:shadow-md hover:ring-border",
      )}
    >
      <div
        className={cn(
          "flex shrink-0 items-center gap-1.5 border-b border-white/10 px-3 py-2",
          editing && "dashboard-drag-handle",
        )}
      >
        {/* The grip's width is reserved in view mode too, so the title doesn't
            shift sideways when you toggle edit — one of the small jumps that
            made the board feel unstable. */}
        {editing ? (
          <GripVertical className="h-3.5 w-3.5 shrink-0 text-muted-foreground/60 animate-in fade-in-0 duration-150" />
        ) : (
          <span className="h-3.5 w-3.5 shrink-0" aria-hidden />
        )}
        <span className="min-w-0 flex-1 truncate text-xs font-medium tracking-tight">{title}</span>

        {/* Controls sit in a FIXED order in both modes — ⓘ, open, then the
            edit-only actions. Previously the whole cluster only existed while
            editing, so a control you'd just used vanished when you left edit
            mode and the ones that remained moved. */}
        {showHelp && (
          <Popover>
            <PopoverTrigger asChild>
              <button
                type="button"
                aria-label={`About ${title}`}
                className="flex h-5 w-5 shrink-0 items-center justify-center rounded text-muted-foreground/70 outline-none transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50"
                onMouseDown={stopDrag}
                onPointerDown={stopDrag}
              >
                <Info className="h-3.5 w-3.5" />
              </button>
            </PopoverTrigger>
            {/* A popover, not a tooltip: `help` is several labelled sections
                and can carry a docs link, so it has to be dismissible and
                selectable rather than vanishing on pointer-out. */}
            <PopoverContent
              align="end"
              className="w-72"
              onMouseDown={stopDrag}
              onPointerDown={stopDrag}
            >
              <PopoverHeader>
                <PopoverTitle>{title}</PopoverTitle>
              </PopoverHeader>
              <WidgetHelpBody help={help} fallback={description} />
            </PopoverContent>
          </Popover>
        )}

        {/* Present in BOTH modes: reading a card and wanting the detail behind
            it is a view-mode action, not an editing one. */}
        {canDrill &&
          (drillHref ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <Link
                  href={drillHref}
                  aria-label={`${drillLabel}: ${title}`}
                  className="flex h-5 w-5 shrink-0 items-center justify-center rounded text-muted-foreground/70 outline-none transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50"
                  onMouseDown={stopDrag}
                  onPointerDown={stopDrag}
                >
                  <ArrowUpRight className="h-3.5 w-3.5" />
                </Link>
              </TooltipTrigger>
              <TooltipContent side="bottom">{drillLabel}</TooltipContent>
            </Tooltip>
          ) : (
            // Disabled rather than hidden: a control that appears only once a
            // log is bound reads as a bug. The tooltip says why.
            <Tooltip>
              <TooltipTrigger asChild>
                <span
                  aria-disabled
                  className="flex h-5 w-5 shrink-0 cursor-not-allowed items-center justify-center rounded text-muted-foreground/30"
                >
                  <ArrowUpRight className="h-3.5 w-3.5" />
                </span>
              </TooltipTrigger>
              <TooltipContent side="bottom">Bind an event log to open this module.</TooltipContent>
            </Tooltip>
          ))}

        {editing && (
          <span className="flex shrink-0 items-center gap-1.5 animate-in fade-in-0 duration-150">
            <Popover>
              <PopoverTrigger asChild>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label={`Configure ${title}`}
                  className="h-6 w-6 shrink-0 text-muted-foreground hover:text-foreground"
                  onMouseDown={stopDrag}
                  onPointerDown={stopDrag}
                >
                  <Settings2 className="h-3.5 w-3.5" />
                </Button>
              </PopoverTrigger>
              <PopoverContent
                align="end"
                className="w-72"
                onMouseDown={stopDrag}
                onPointerDown={stopDrag}
              >
                <PopoverHeader>
                  <PopoverTitle>Card settings</PopoverTitle>
                </PopoverHeader>
                {isViz ? (
                  <VizSettings item={item} logId={logId} onChange={onUpdate} />
                ) : (
                  <CardConfigForm item={item} schema={schema} onChange={onUpdate} />
                )}
              </PopoverContent>
            </Popover>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={`Remove ${title}`}
              className="h-6 w-6 shrink-0 text-muted-foreground hover:text-destructive"
              onMouseDown={stopDrag}
              onPointerDown={stopDrag}
              onClick={onRemove}
            >
              <X className="h-3.5 w-3.5" />
            </Button>
          </span>
        )}
      </div>
      {/* `overflow-hidden`, not `auto`: the card frame must never be a second
          scroll container. `CardShell` inside the widget owns scrolling, so a
          too-small card can't produce a scrollbar nested in a scrollbar.

          While a filter commit refetches, the body dims and stops taking
          clicks but keeps rendering the previous result — the board never
          throws away what it was showing before it has a replacement. */}
      <div
        className={cn(
          "min-h-0 flex-1 overflow-hidden p-3 animate-in fade-in-0 duration-300",
          "transition-opacity",
          refetching && "pointer-events-none opacity-60",
        )}
      >
        {isViz ? (
          <GenericVizBody item={item} logId={logId} />
        ) : (
          // Scoped so a widget that mounts its module's settings provider gets
          // THIS card's bucket rather than the panel's — that is what lets one
          // card differ from another, and from the panel.
          <CardVizScope
            cardId={item.i}
            logId={logId}
            config={item.config}
            onConfigChange={(config) => onUpdate({ config })}
          >
            <WidgetBody item={item} logId={logId} onDrill={onDrill} />
          </CardVizScope>
        )}
      </div>
    </div>
  );
});

/** Module-authored widget body (the original card path). Only rendered for
 * `kind:"widget"` items, so `module_id`/`widget_id` are always present. */
function WidgetBody({
  item,
  logId,
  onDrill,
}: {
  item: DashboardItem;
  logId: string | null;
  onDrill?: DrillHandler;
}) {
  const Widget = useWidget(item.module_id ?? "", item.widget_id ?? "");
  if (!logId) {
    return (
      <div className="flex h-full items-center justify-center text-center text-xs text-muted-foreground">
        Select an event log to populate this card.
      </div>
    );
  }
  // Strip the platform's reserved keys so the module widget only ever sees its
  // own declared options — the filter is applied to requests, and the render
  // settings reach the widget through the settings store, not through config.
  return (
    <Widget
      logId={logId}
      moduleId={item.module_id ?? ""}
      config={configWithoutRender(configWithoutWidgetFilter(item.config))}
      onDrill={onDrill}
    />
  );
}
