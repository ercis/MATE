"use client";

import { useState, type ReactNode } from "react";
import { Settings, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/cn";
import { useDashboardFilter } from "@/components/dashboards/dashboard-filter";
import {
  type CanvasSettings,
  type CardChrome,
  type FilterPreset,
} from "@/lib/dashboard-queries";

const NONE = "__none__";

/**
 * One row of the saved-filter list. Fixed `h-10` so the "no filter" row (a bare
 * line of text) and the preset rows (which carry a 28px delete button) keep the
 * same rhythm instead of the list going ragged at the first preset.
 */
const ROW =
  "flex h-10 cursor-pointer items-center gap-3 px-3 text-sm font-normal transition-colors hover:bg-muted/40 has-[[data-state=checked]]:bg-muted/70";

function newId(): string {
  return typeof crypto !== "undefined" && crypto.randomUUID
    ? crypto.randomUUID()
    : `preset-${Date.now()}`;
}

/**
 * The dashboard's board-level settings, gathered into one popup: canvas grid,
 * card appearance, and saved filters. Everything edits the board's
 * `CanvasSettings` (via `onChange`) so it rides the normal Save path and shows
 * up in view mode. Saved filters additionally apply *live* to the ephemeral
 * filter bar (through `useDashboardFilter`) so the change is visible immediately.
 */
export function DashboardSettingsDialog({
  settings,
  onChange,
}: {
  settings: CanvasSettings;
  onChange: (next: CanvasSettings) => void;
}) {
  const { columnFilters, setColumnFilters } = useDashboardFilter();
  const [newName, setNewName] = useState("");

  const setChrome = (patch: Partial<CardChrome>) =>
    onChange({ ...settings, chrome: { ...settings.chrome, ...patch } });

  // Selecting a saved filter marks it active (persisted) AND loads its filters
  // into the live bar so the board reflects it without leaving the dialog.
  const applyPreset = (id: string | null) => {
    const preset = id ? settings.presets.find((p) => p.id === id) : null;
    onChange({ ...settings, active_preset_id: preset ? preset.id : null });
    setColumnFilters(preset ? preset.filters : []);
  };

  const canSave = newName.trim().length > 0 && columnFilters.length > 0;

  const saveCurrentAsPreset = () => {
    const name = newName.trim();
    if (!name || columnFilters.length === 0) return;
    const preset: FilterPreset = { id: newId(), name, filters: columnFilters };
    onChange({
      ...settings,
      presets: [...settings.presets, preset],
      active_preset_id: preset.id,
    });
    setNewName("");
  };

  const deletePreset = (id: string) => {
    const wasActive = settings.active_preset_id === id;
    onChange({
      ...settings,
      presets: settings.presets.filter((p) => p.id !== id),
      active_preset_id: wasActive ? null : settings.active_preset_id,
    });
    if (wasActive) setColumnFilters([]);
  };

  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button type="button" variant="outline" size="sm">
          <Settings className="mr-1.5 h-3.5 w-3.5" />
          Settings
        </Button>
      </DialogTrigger>
      {/* `sm:max-w-md`, not `max-w-md`: DialogContent already ships
          `sm:max-w-lg`, and a breakpoint variant outranks a bare utility above
          `sm` – so the plain class was dead and this panel rendered 512px wide
          around two controls. */}
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Dashboard settings</DialogTitle>
          <DialogDescription>
            Saved with the board and applied in view mode.
          </DialogDescription>
        </DialogHeader>

        {/* The grid-snapping picker used to live here. It is gone: the board
            is one fixed 12-column grid, because a per-board column count made
            every widget's declared minimum size mean something different on
            every board. */}

        <div className="space-y-5">
          {/* Card appearance */}
          <Section title="Card appearance">
            <Label className="flex cursor-pointer items-center justify-between gap-3 rounded-md border border-border bg-card px-3 py-2.5 leading-normal">
              <span className="space-y-0.5">
                <span className="block text-sm font-normal">Border</span>
                <span className="block text-xs font-normal text-muted-foreground">
                  Show a thin outline around every card on this board.
                </span>
              </span>
              <Switch
                checked={settings.chrome.border}
                onCheckedChange={(v) => setChrome({ border: v })}
                className="cursor-pointer"
              />
            </Label>
          </Section>

          {/* Saved filters (persisted presets) */}
          <Section
            title="Saved filters"
            description="Reusable filter sets. The active one loads with the board."
          >
            <div className="space-y-3">
              <RadioGroup
                value={settings.active_preset_id ?? NONE}
                onValueChange={(v) => applyPreset(v === NONE ? null : v)}
                className="gap-0 overflow-hidden rounded-md border border-border"
              >
                {/* One scroll box around the whole list. The previous
                    ScrollArea wrapped the presets only – it indented them 8px
                    past the row above it, and never actually clamped, since
                    its viewport is `h-full` inside an auto-height root. */}
                <div className="max-h-52 divide-y divide-border overflow-y-auto">
                  <Label htmlFor="seg-none" className={ROW}>
                    <RadioGroupItem value={NONE} id="seg-none" />
                    No filter (full log)
                  </Label>
                  {settings.presets.map((p) => (
                    <div key={p.id} className={cn(ROW, "group")}>
                      <RadioGroupItem value={p.id} id={`seg-${p.id}`} />
                      <Label
                        htmlFor={`seg-${p.id}`}
                        className="flex min-w-0 flex-1 cursor-pointer items-center justify-between gap-3 font-normal"
                      >
                        <span className="truncate">{p.name}</span>
                        <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
                          {p.filters.length} filter{p.filters.length === 1 ? "" : "s"}
                        </span>
                      </Label>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        aria-label={`Delete ${p.name}`}
                        className="h-7 w-7 shrink-0 text-muted-foreground opacity-0 hover:text-destructive focus-visible:opacity-100 group-hover:opacity-100"
                        onClick={() => deletePreset(p.id)}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  ))}
                </div>
              </RadioGroup>

              {/* Capture the live filter bar as a new named set. */}
              <div className="space-y-1.5">
                <Label htmlFor="preset-name" className="text-xs text-muted-foreground">
                  Save current filters
                </Label>
                <div className="flex items-center gap-2">
                  <Input
                    id="preset-name"
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                    placeholder="Name this filter set…"
                    className="h-8 text-sm"
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        saveCurrentAsPreset();
                      }
                    }}
                  />
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={!canSave}
                    onClick={saveCurrentAsPreset}
                  >
                    Save
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground">
                  {columnFilters.length === 0
                    ? "Add filters in the bar above, then save them here."
                    : `Captures the ${columnFilters.length} active filter${
                        columnFilters.length === 1 ? "" : "s"
                      } from the bar.`}
                </p>
              </div>
            </div>
          </Section>
        </div>
      </DialogContent>
    </Dialog>
  );
}

/** A titled settings group. Separated by whitespace, not rules: both groups
 * already carry their own bordered surface, so a divider on top of that reads
 * as a box inside a box. */
function Section({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <section className="space-y-2">
      <div className="space-y-0.5">
        <h3 className="text-sm font-medium leading-none">{title}</h3>
        {description && <p className="text-xs text-muted-foreground">{description}</p>}
      </div>
      {children}
    </section>
  );
}
