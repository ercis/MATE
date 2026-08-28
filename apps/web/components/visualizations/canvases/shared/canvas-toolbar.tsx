"use client";

// The canvas control chrome every canvas view shares – the top-right control
// cluster (zoom / fit / settings / reset / fullscreen) plus the row primitives
// its settings popover is built from.
//
// `CanvasShell` renders the cluster for React-Flow canvases automatically; the
// standalone bpmn-js viewers (discovery / conformance / process comparison)
// render `CanvasControlCluster` directly so they end up pixel-identical. A
// canvas must NEVER hand-roll this pill or put a filter bar above itself – the
// DFG canvas is the reference: full-bleed graph, every control in the cluster.
//
// Exposed to module bundles as a runtime external (`runtime-externals.json` +
// `module-runtime.ts`), so keep the surface small and depend only on other
// runtime-external `@/` paths.

import { useState, type ReactNode } from "react";
import { Frame, Loader2, Minus, Plus, RotateCcw, Search, SlidersHorizontal } from "lucide-react";
import { Popover as PopoverPrimitive } from "radix-ui";

import { cn } from "@/lib/cn";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

import { CanvasFullscreenButton } from "./canvas-controls";

// --------------------------------------------------------------------------
// Control cluster
// --------------------------------------------------------------------------

export interface CanvasSettingsSlotProps {
  /** Popover body – compose it from the `CanvasSetting*` primitives below. */
  settings?: ReactNode;
  /** aria-label + title of the settings trigger. */
  settingsLabel?: string;
  /** Extra classes for the popover content (width overrides etc.). */
  settingsClassName?: string;
}

export interface CanvasResetSlotProps {
  /** Discards dragged node positions / re-applies the auto layout. Omit on
   *  canvases whose layout can't be dragged (the button then doesn't render). */
  onReset?: () => void;
  resetLabel?: string;
  resetTitle?: string;
  resetDescription?: string;
  resetConfirmLabel?: string;
}

export interface CanvasControlClusterProps extends CanvasSettingsSlotProps, CanvasResetSlotProps {
  onZoomIn: () => void;
  onZoomOut: () => void;
  onFit: () => void;
  isFullscreen: boolean;
  onToggleFullscreen: () => void;
  /** Rare canvas-specific buttons appended after settings + reset (still left
   *  of fullscreen, which always stays last). */
  extra?: ReactNode;
}

/**
 * The one canvas control cluster: `[−] [+] [fit] [settings] [reset] [fullscreen]`
 * in a single top-right pill. Order and styling are fixed on purpose – uniform
 * across React-Flow canvases, bpmn-js viewers and anything added later.
 * Fullscreen is pinned to the far right so it sits in the same spot on every
 * canvas regardless of which optional controls that canvas renders.
 */
export function CanvasControlCluster({
  onZoomIn,
  onZoomOut,
  onFit,
  isFullscreen,
  onToggleFullscreen,
  settings,
  settingsLabel,
  settingsClassName,
  onReset,
  resetLabel,
  resetTitle,
  resetDescription,
  resetConfirmLabel,
  extra,
}: CanvasControlClusterProps) {
  return (
    <div className="pointer-events-none absolute right-3 top-3 z-10 flex gap-1.5">
      <div className="pointer-events-auto flex items-center gap-1 rounded-md border bg-card/80 p-1 shadow-sm backdrop-blur">
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7 cursor-pointer"
          onClick={onZoomOut}
          aria-label="Zoom out"
          title="Zoom out"
        >
          <Minus className="h-3.5 w-3.5" />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7 cursor-pointer"
          onClick={onZoomIn}
          aria-label="Zoom in"
          title="Zoom in"
        >
          <Plus className="h-3.5 w-3.5" />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7 cursor-pointer"
          onClick={onFit}
          aria-label="Fit to view"
          title="Fit to view"
        >
          <Frame className="h-3.5 w-3.5" />
        </Button>
        {settings ? (
          <CanvasSettingsPopover label={settingsLabel} contentClassName={settingsClassName}>
            {settings}
          </CanvasSettingsPopover>
        ) : null}
        {onReset ? (
          <CanvasResetButton
            onReset={onReset}
            label={resetLabel}
            title={resetTitle}
            description={resetDescription}
            confirmLabel={resetConfirmLabel}
          />
        ) : null}
        {extra}
        {/* Always last: fullscreen is the rightmost control on every canvas. */}
        <CanvasFullscreenButton isFullscreen={isFullscreen} onToggle={onToggleFullscreen} />
      </div>
    </div>
  );
}

/**
 * Top-centre chip for work that runs while the current graph stays on screen –
 * a refetch or re-layout triggered from the settings popover. Unmounting the
 * canvas for a skeleton would close that popover mid-interaction, so canvases
 * keep rendering and show this instead.
 */
export function CanvasBusyChip({ label = "Working…" }: { label?: string }) {
  return (
    <div className="pointer-events-none absolute left-1/2 top-3 z-10 flex -translate-x-1/2 items-center gap-2 rounded-md border bg-card/85 px-2.5 py-1.5 text-xs text-muted-foreground shadow-sm backdrop-blur">
      <Loader2 className="h-3.5 w-3.5 animate-spin" />
      {label}
    </div>
  );
}

/**
 * Ghost icon-button + popover matching the canvas control cluster. Renders
 * `children` as the popover body (the canvas-specific render settings). The
 * trigger is the standard "sliders" settings affordance so the control reads
 * identically across every canvas.
 */
export function CanvasSettingsPopover({
  children,
  label = "Graph settings",
  contentClassName,
}: {
  children: ReactNode;
  /** aria-label + title for the trigger button. */
  label?: string;
  /** Extra classes for the popover content (width overrides etc.). */
  contentClassName?: string;
}) {
  return (
    <PopoverPrimitive.Root>
      <PopoverPrimitive.Trigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7 cursor-pointer"
          aria-label={label}
          title={label}
        >
          <SlidersHorizontal className="h-3.5 w-3.5" />
        </Button>
      </PopoverPrimitive.Trigger>
      <PopoverPrimitive.Portal>
        <PopoverPrimitive.Content
          side="left"
          align="start"
          sideOffset={10}
          collisionPadding={12}
          className={cn(
            "z-50 max-h-[70vh] w-80 overflow-y-auto rounded-lg border bg-popover p-4 text-popover-foreground shadow-md outline-none",
            "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95",
            "data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95",
            contentClassName,
          )}
        >
          {children}
        </PopoverPrimitive.Content>
      </PopoverPrimitive.Portal>
    </PopoverPrimitive.Root>
  );
}

/**
 * Ghost icon-button that discards dragged node positions and re-applies the
 * auto layout, behind a confirm dialog. The dialog copy is overridable so a
 * canvas can name what gets reset; the default matches the DFG wording.
 */
export function CanvasResetButton({
  onReset,
  label = "Reset layout",
  title = "Reset layout?",
  description = "All dragged node positions for this view will be discarded and the auto-layout will be reapplied. This cannot be undone.",
  confirmLabel = "Reset",
}: {
  onReset: () => void;
  /** aria-label + title for the trigger button. */
  label?: string;
  title?: string;
  description?: string;
  confirmLabel?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button
        variant="ghost"
        size="icon"
        className="h-7 w-7 cursor-pointer"
        aria-label={label}
        title={label}
        onClick={() => setOpen(true)}
      >
        <RotateCcw className="h-3.5 w-3.5" />
      </Button>

      <AlertDialog open={open} onOpenChange={setOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{title}</AlertDialogTitle>
            <AlertDialogDescription>{description}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                onReset();
                setOpen(false);
              }}
            >
              {confirmLabel}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

// --------------------------------------------------------------------------
// Settings popover primitives
//
// Every canvas builds its popover from these so label sizes, control widths and
// row rhythm match across modules. Don't hand-roll rows.
// --------------------------------------------------------------------------

/** Vertical rhythm wrapper for a popover body. */
export function CanvasSettings({ children }: { children: ReactNode }) {
  return <div className="space-y-4">{children}</div>;
}

/** Titled group inside a popover – used when a canvas has >4 settings. */
export function CanvasSettingsSection({
  title,
  children,
  first = false,
}: {
  title: string;
  children: ReactNode;
  /** Skip the leading separator (the first section in a popover). */
  first?: boolean;
}) {
  return (
    <div className="space-y-3">
      {first ? null : <Separator />}
      <div className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/70">
        {title}
      </div>
      <div className="space-y-4">{children}</div>
    </div>
  );
}

/** Label + control on one line – the default row. */
export function CanvasSettingsRow({
  label,
  children,
  hint,
}: {
  label: string;
  children: ReactNode;
  /** Native tooltip on the label (kept native: floating tooltips fire on
   *  popover-open when the pointer already sits over the row). */
  hint?: string;
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <Label className="text-xs font-normal text-muted-foreground" title={hint}>
        {label}
      </Label>
      {children}
    </div>
  );
}

export interface CanvasSettingsOption<T extends string> {
  value: T;
  label: string;
}

/** Label + `Select` row. */
export function CanvasSettingsSelect<T extends string>({
  label,
  value,
  onChange,
  options,
  hint,
  disabled,
  className = "w-44",
}: {
  label: string;
  value: T;
  onChange: (v: T) => void;
  options: readonly CanvasSettingsOption<T>[];
  hint?: string;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <CanvasSettingsRow label={label} hint={hint}>
      <Select value={value} onValueChange={(v) => onChange(v as T)} disabled={disabled}>
        <SelectTrigger className={cn("h-7 text-xs", className)}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((o) => (
            <SelectItem key={o.value} value={o.value}>
              {o.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </CanvasSettingsRow>
  );
}

/** Label + `Switch` row. */
export function CanvasSettingsSwitch({
  label,
  checked,
  onChange,
  hint,
  disabled,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  hint?: string;
  disabled?: boolean;
}) {
  return (
    <CanvasSettingsRow label={label} hint={hint}>
      <Switch checked={checked} onCheckedChange={onChange} disabled={disabled} />
    </CanvasSettingsRow>
  );
}

/** Label + segmented button group – for 2–3 mutually exclusive short options. */
export function CanvasSettingsSegmented<T extends string>({
  label,
  value,
  onChange,
  options,
  hint,
}: {
  label: string;
  value: T;
  onChange: (v: T) => void;
  options: readonly CanvasSettingsOption<T>[];
  hint?: string;
}) {
  return (
    <CanvasSettingsRow label={label} hint={hint}>
      <div className="inline-flex items-center gap-0.5 rounded-md bg-muted p-0.5">
        {options.map((o) => {
          const active = o.value === value;
          return (
            <button
              key={o.value}
              type="button"
              aria-pressed={active}
              data-state={active ? "active" : "inactive"}
              onClick={() => onChange(o.value)}
              className={cn(
                "cursor-pointer rounded px-2 py-1 text-xs font-medium transition-all",
                active
                  ? "bg-background text-foreground shadow-sm"
                  : "text-foreground/60 hover:text-foreground",
              )}
            >
              {o.label}
            </button>
          );
        })}
      </div>
    </CanvasSettingsRow>
  );
}

/**
 * Label + value readout above a full-width slider.
 *
 * `onCommit` (release) is what expensive settings should use – a re-mine or
 * refetch per drag step would queue one request per pixel. `onChange` fires per
 * step and is for cheap client-side re-renders. Pass `badge` for an extra
 * affordance next to the value (the DFG's "Auto" pill).
 */
export function CanvasSettingsSlider({
  label,
  value,
  onChange,
  onCommit,
  min = 0,
  max = 1,
  step = 0.05,
  format,
  badge,
  hint,
}: {
  label: string;
  value: number;
  onChange?: (v: number) => void;
  onCommit?: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
  /** Value readout. Defaults to 2 decimals. */
  format?: (v: number) => string;
  badge?: ReactNode;
  hint?: string;
}) {
  const safe = Number.isFinite(value) ? value : min;
  // Local mirror so the handle tracks the pointer even when the parent only
  // reacts on commit.
  const [local, setLocal] = useState(safe);
  const [dragging, setDragging] = useState(false);
  const shown = dragging ? local : safe;

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <Label className="text-xs font-normal text-muted-foreground" title={hint}>
          {label}
        </Label>
        <div className="flex items-center gap-2">
          <span className="text-xs tabular-nums text-muted-foreground">
            {format ? format(shown) : shown.toFixed(2)}
          </span>
          {badge}
        </div>
      </div>
      <Slider
        value={[shown]}
        min={min}
        max={max}
        step={step}
        onValueChange={(v) => {
          const next = v[0] ?? min;
          setDragging(true);
          setLocal(next);
          onChange?.(next);
        }}
        onValueCommit={(v) => {
          setDragging(false);
          onCommit?.(v[0] ?? min);
        }}
      />
    </div>
  );
}

/** Toggle pill (the DFG's "Auto" affordance) for use as a slider `badge`. */
export function CanvasSettingsBadgeToggle({
  label,
  active,
  onActivate,
  title,
}: {
  label: string;
  active: boolean;
  onActivate: () => void;
  title?: string;
}) {
  return (
    <button
      type="button"
      onClick={active ? undefined : onActivate}
      aria-pressed={active}
      title={title}
      className={cn(
        "h-5 shrink-0 rounded-md border px-1.5 text-[10px] font-semibold uppercase tracking-wider transition-colors",
        active
          ? "cursor-default border-primary/40 bg-primary/15 text-primary"
          : "cursor-pointer border-border bg-transparent text-muted-foreground hover:border-primary/40 hover:text-foreground",
      )}
    >
      {label}
    </button>
  );
}

/** Search field row – Enter or the button submits the trimmed query. */
export function CanvasSettingsSearch({
  label = "Find",
  placeholder,
  onSearch,
}: {
  label?: string;
  placeholder?: string;
  onSearch: (query: string) => void;
}) {
  const [text, setText] = useState("");
  const submit = () => {
    const q = text.trim();
    if (q) onSearch(q);
  };
  return (
    <CanvasSettingsRow label={label}>
      <div className="flex items-center gap-1">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") submit();
          }}
          placeholder={placeholder}
          className="h-7 w-36 rounded-md border bg-background px-2 text-xs outline-none focus-visible:ring-1 focus-visible:ring-ring"
        />
        <Button
          variant="outline"
          size="icon"
          className="h-7 w-7 shrink-0 cursor-pointer"
          onClick={submit}
          aria-label="Search"
        >
          <Search className="h-3.5 w-3.5" />
        </Button>
      </div>
    </CanvasSettingsRow>
  );
}

/** Full-width action button(s) at the bottom of a popover (export, download…). */
export function CanvasSettingsActions({ children }: { children: ReactNode }) {
  return (
    <>
      <Separator />
      <div className="flex flex-col gap-2">{children}</div>
    </>
  );
}
