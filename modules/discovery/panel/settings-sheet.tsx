"use client";

import { useEffect, useState } from "react";
import { ArrowDown, X } from "lucide-react";
import { Dialog as SheetPrimitive, VisuallyHidden } from "radix-ui";

import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/cn";

import {
  useGeneralSettings,
  useGeneralSettingsSetter,
  useModuleConfig,
  useModuleConfigSchema,
  useResetGeneralSettings,
  useUpdateModuleConfig,
} from "./discovery-settings-context";
import {
  DEFAULT_GENERAL,
  type EdgeRouting,
  type FrequencyDisplayMode,
  type LayoutDirection,
  type Theme,
} from "@/lib/stores/visualization-settings";

interface SettingsSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function SettingsSheet({ open, onOpenChange }: SettingsSheetProps) {
  return (
    <SheetPrimitive.Root open={open} onOpenChange={onOpenChange} modal={false}>
      <SheetPrimitive.Portal>
        <SheetPrimitive.Content
          aria-label="Visualisation settings"
          onInteractOutside={(e) => e.preventDefault()}
          onOpenAutoFocus={(e) => e.preventDefault()}
          className={cn(
            "fixed inset-y-0 right-0 z-50 flex w-[440px] max-w-[100vw] flex-col border-l border-border bg-background shadow-lg",
            "transition ease-in-out",
            "data-[state=closed]:animate-out data-[state=closed]:duration-300 data-[state=open]:animate-in data-[state=open]:duration-500",
            "data-[state=closed]:slide-out-to-right data-[state=open]:slide-in-from-right",
          )}
        >
          <VisuallyHidden.Root>
            <SheetPrimitive.Title>Visualisation settings</SheetPrimitive.Title>
            <SheetPrimitive.Description>
              General appearance and module-specific configuration for this discovery module.
            </SheetPrimitive.Description>
          </VisuallyHidden.Root>
          <header className="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
            <div className="space-y-0.5">
              <h2 className="text-sm font-semibold tracking-tight">Configure</h2>
              <p className="text-xs text-muted-foreground">
                General appearance and module-specific settings.
              </p>
            </div>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label="Close settings"
              onClick={() => onOpenChange(false)}
              className="h-8 w-8 cursor-pointer text-muted-foreground"
            >
              <X className="h-4 w-4" />
            </Button>
          </header>

          <div className="flex min-h-0 flex-1 flex-col overflow-y-auto px-4 py-4">
            <MergedSettingsForm onClose={() => onOpenChange(false)} />
          </div>
        </SheetPrimitive.Content>
      </SheetPrimitive.Portal>
    </SheetPrimitive.Root>
  );
}

// --------------------------------------------------------------------------
// Merged form: general (Zustand, instant) + module (server, draft-then-save)
// --------------------------------------------------------------------------

function MergedSettingsForm({ onClose }: { onClose: () => void }) {
  const general = useGeneralSettings();
  const setGeneral = useGeneralSettingsSetter();
  const resetGeneral = useResetGeneralSettings();

  const { data: schema, isLoading: schemaLoading } = useModuleConfigSchema();
  const { data: stored, isLoading: configLoading } = useModuleConfig();
  const update = useUpdateModuleConfig();

  const properties = (schema as ConfigSchema | undefined)?.properties ?? {};
  const [draft, setDraft] = useState<Record<string, unknown>>({});

  useEffect(() => {
    if (stored) setDraft({ ...stored.config });
  }, [stored]);

  const entries = Object.entries(properties);
  const moduleLoading = schemaLoading || configLoading;
  const moduleHasFields = entries.length > 0;

  const onSaveModule = async () => {
    await update.mutateAsync({
      config: draft,
      enabled: stored?.enabled ?? true,
    });
    onClose();
  };

  const onResetModuleDefaults = () => {
    const fresh: Record<string, unknown> = {};
    for (const [key, prop] of entries) {
      if (prop.default !== undefined) fresh[key] = prop.default;
    }
    setDraft(fresh);
  };

  const onResetAll = () => {
    resetGeneral();
    onResetModuleDefaults();
  };

  return (
    <div className="space-y-6 pb-2">
      <SettingGroup title="Layout" description="Direction and routing of auto-laid graphs.">
        <FieldRow label="Direction">
          <DirectionToggle
            value={general.layoutDirection}
            onChange={(v) => setGeneral({ layoutDirection: v })}
          />
        </FieldRow>
        <FieldRow label="Edge routing">
          <Select
            value={general.edgeRouting}
            onValueChange={(v) => setGeneral({ edgeRouting: v as EdgeRouting })}
          >
            <SelectTrigger className="h-8 w-44 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="orthogonal">Orthogonal</SelectItem>
              <SelectItem value="spline">Spline</SelectItem>
              <SelectItem value="straight">Straight</SelectItem>
            </SelectContent>
          </Select>
        </FieldRow>
      </SettingGroup>

      <Separator />

      <SettingGroup title="Display" description="Chrome and labels.">
        <FieldRow label="Show minimap">
          <Switch
            checked={general.showMinimap}
            onCheckedChange={(v) => setGeneral({ showMinimap: v })}
          />
        </FieldRow>
        <FieldRow label="Show grid background">
          <Switch
            checked={general.showGrid}
            onCheckedChange={(v) => setGeneral({ showGrid: v })}
          />
        </FieldRow>
        <FieldRow label="Node label max length">
          <div className="flex items-center gap-3 w-44">
            <Slider
              value={[general.nodeLabelMaxLength]}
              min={6}
              max={64}
              step={2}
              onValueChange={(v) => setGeneral({ nodeLabelMaxLength: v[0] ?? 32 })}
            />
            <span className="text-xs tabular-nums w-6 text-right text-muted-foreground">
              {general.nodeLabelMaxLength}
            </span>
          </div>
        </FieldRow>
        <FieldRow label="Frequency display">
          <Select
            value={general.frequencyDisplayMode}
            onValueChange={(v) => setGeneral({ frequencyDisplayMode: v as FrequencyDisplayMode })}
          >
            <SelectTrigger className="h-8 w-44 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="count">Count</SelectItem>
              <SelectItem value="ratio">Ratio</SelectItem>
              <SelectItem value="per-case">Per case</SelectItem>
            </SelectContent>
          </Select>
        </FieldRow>
        <FieldRow label="Color intensity">
          <div className="flex items-center gap-3 w-44">
            <Slider
              value={[general.colorIntensity]}
              min={0}
              max={1}
              step={0.05}
              onValueChange={(v) => setGeneral({ colorIntensity: v[0] ?? 0.6 })}
            />
            <span className="text-xs tabular-nums w-10 text-right text-muted-foreground">
              {general.colorIntensity.toFixed(2)}
            </span>
          </div>
        </FieldRow>
        <FieldRow label="Theme">
          <Select value={general.theme} onValueChange={(v) => setGeneral({ theme: v as Theme })}>
            <SelectTrigger className="h-8 w-44 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="default">Default</SelectItem>
              <SelectItem value="monochrome">Monochrome</SelectItem>
              <SelectItem value="colorblind">Colorblind-safe</SelectItem>
            </SelectContent>
          </Select>
        </FieldRow>
      </SettingGroup>

      <Separator />

      <SettingGroup
        title="Module"
        description="Compute knobs exposed by this module. Saving re-runs discovery."
      >
        {moduleLoading ? (
          <p className="text-xs text-muted-foreground">Loading…</p>
        ) : !moduleHasFields ? (
          <p className="text-xs text-muted-foreground">
            This module exposes no configurable settings.
          </p>
        ) : (
          entries.map(([key, prop]) => {
            const value = draft[key] ?? prop.default;
            return (
              <SchemaField
                key={key}
                propKey={key}
                prop={prop}
                value={value}
                onChange={(v) => setDraft((d) => ({ ...d, [key]: v }))}
              />
            );
          })
        )}
      </SettingGroup>

      <Separator />

      <div className="flex items-center justify-between gap-2 pt-1">
        <Button
          variant="ghost"
          size="sm"
          className="cursor-pointer"
          onClick={onResetAll}
        >
          Reset to defaults
        </Button>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" className="cursor-pointer" onClick={onClose}>
            Cancel
          </Button>
          {moduleHasFields && (
            <Button
              size="sm"
              className="cursor-pointer"
              onClick={onSaveModule}
              disabled={update.isPending}
            >
              {update.isPending ? "Saving…" : "Save"}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------
// Direction toggle – rotating arrow that cycles TB → LR → BT → RL.
// Keeps a monotonically-increasing tick so each click rotates +90°
// rather than snapping back at the wrap-around.
// --------------------------------------------------------------------------

const DIRECTION_ORDER: LayoutDirection[] = ["TB", "LR", "BT", "RL"];
const DIRECTION_LABEL: Record<LayoutDirection, string> = {
  TB: "Top → Bottom",
  LR: "Left → Right",
  BT: "Bottom → Top",
  RL: "Right → Left",
};

function DirectionToggle({
  value,
  onChange,
}: {
  value: LayoutDirection;
  onChange: (v: LayoutDirection) => void;
}) {
  const initialIdx = Math.max(0, DIRECTION_ORDER.indexOf(value));
  const [tick, setTick] = useState(initialIdx);

  // Keep tick in sync with external changes (e.g. reset) by advancing to the
  // next forward-aligned position, so the arrow never visually snaps back.
  useEffect(() => {
    setTick((prev) => {
      const targetIdx = Math.max(0, DIRECTION_ORDER.indexOf(value));
      const curMod = ((prev % DIRECTION_ORDER.length) + DIRECTION_ORDER.length) % DIRECTION_ORDER.length;
      const forwardDelta = (targetIdx - curMod + DIRECTION_ORDER.length) % DIRECTION_ORDER.length;
      return prev + forwardDelta;
    });
  }, [value]);

  const onClick = () => {
    const next = tick + 1;
    setTick(next);
    onChange(DIRECTION_ORDER[next % DIRECTION_ORDER.length]!);
  };

  return (
    <div className="flex items-center gap-2">
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="h-8 w-8 cursor-pointer p-0"
        onClick={onClick}
        aria-label={`Layout direction ${DIRECTION_LABEL[value]}. Click to rotate.`}
      >
        <ArrowDown
          className="h-3.5 w-3.5 transition-transform duration-300"
          style={{ transform: `rotate(${tick * 90}deg)` }}
        />
      </Button>
      <span className="text-xs tabular-nums text-muted-foreground">{value}</span>
    </div>
  );
}

// --------------------------------------------------------------------------
// Module schema-driven fields
// --------------------------------------------------------------------------

interface SchemaProperty {
  type: "number" | "boolean" | "string" | "enum";
  title?: string;
  description?: string;
  minimum?: number;
  maximum?: number;
  step?: number;
  default?: unknown;
  enum?: string[];
  ui?: {
    widget?: "slider" | "switch" | "select" | "radio";
    group?: "general" | "viz";
    affects?: "compute" | "render";
  };
}

interface ConfigSchema {
  properties?: Record<string, SchemaProperty>;
}

function SchemaField({
  propKey,
  prop,
  value,
  onChange,
}: {
  propKey: string;
  prop: SchemaProperty;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  const widget = prop.ui?.widget ?? defaultWidgetForType(prop);
  const compute = prop.ui?.affects === "compute";

  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between">
        <Label className="text-sm font-medium">{prop.title ?? propKey}</Label>
        {compute && (
          <span className="text-[10px] uppercase tracking-wide text-amber-600 dark:text-amber-400">
            recomputes
          </span>
        )}
      </div>
      {prop.description && (
        <p className="text-xs text-muted-foreground">{prop.description}</p>
      )}
      <div>
        {widget === "slider" && prop.type === "number" && (
          <div className="flex items-center gap-3">
            <Slider
              value={[Number(value ?? prop.default ?? 0)]}
              min={prop.minimum ?? 0}
              max={prop.maximum ?? 1}
              step={prop.step ?? 0.05}
              onValueChange={(v) => onChange(v[0])}
            />
            <span className="text-xs tabular-nums w-12 text-right text-muted-foreground">
              {Number(value ?? prop.default ?? 0).toFixed(2)}
            </span>
          </div>
        )}
        {widget === "switch" && prop.type === "boolean" && (
          <Switch
            checked={Boolean(value)}
            onCheckedChange={(v) => onChange(v)}
          />
        )}
        {widget === "select" && (prop.type === "enum" || prop.type === "string") && (
          <Select value={String(value ?? "")} onValueChange={(v) => onChange(v)}>
            <SelectTrigger className="h-8 w-full text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {(prop.enum ?? []).map((opt) => (
                <SelectItem key={opt} value={opt}>
                  {opt}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        {widget === "radio" && prop.enum && (
          <RadioGroup
            value={String(value ?? "")}
            onValueChange={(v) => onChange(v)}
            className="flex flex-wrap gap-3"
          >
            {prop.enum.map((opt) => (
              <Label
                key={opt}
                className="flex items-center gap-1.5 text-xs cursor-pointer"
                htmlFor={`${propKey}-${opt}`}
              >
                <RadioGroupItem value={opt} id={`${propKey}-${opt}`} />
                {opt}
              </Label>
            ))}
          </RadioGroup>
        )}
      </div>
    </div>
  );
}

function defaultWidgetForType(prop: SchemaProperty): "slider" | "switch" | "select" | "radio" {
  if (prop.type === "boolean") return "switch";
  if (prop.type === "number") return "slider";
  if (prop.type === "enum") return "select";
  return "select";
}

// --------------------------------------------------------------------------
// Layout helpers
// --------------------------------------------------------------------------

function SettingGroup({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-3">
      <div className="space-y-0.5">
        <h3 className="text-sm font-medium leading-none">{title}</h3>
        {description && <p className="text-xs text-muted-foreground">{description}</p>}
      </div>
      <div className="space-y-3">{children}</div>
    </section>
  );
}

function FieldRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <Label className="text-xs text-muted-foreground font-normal">{label}</Label>
      {children}
    </div>
  );
}

// Re-export defaults for diagnostics / story-style usage.
export { DEFAULT_GENERAL };
