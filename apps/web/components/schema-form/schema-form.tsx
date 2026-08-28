"use client";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/cn";
import { InfoHint } from "@/components/dashboards/kit/info-hint";
import {
  groupProperties,
  type ConfigSchema,
  type PropSchema,
  type SchemaFormDensity,
} from "@/components/schema-form/types";

/**
 * The single renderer for the module config-schema dialect.
 *
 * There used to be two — one for the module settings page, one for dashboard
 * cards — and they had drifted into supporting different controls. The module
 * one grouped fields by `ui.group` but had no boolean and no number input, and
 * gated sliders on `type === "number"`, so every `type: integer` slider in the
 * repo silently rendered as a text input and every boolean as a text box
 * reading "true". The card one handled those but ignored grouping.
 *
 * This is the union. A field renders the same way wherever it appears, which is
 * the point: a card's settings should not be a lesser version of the module's.
 */
export function SchemaForm({
  schema,
  values,
  onChange,
  disabled = false,
  density = "comfortable",
  /** Render only these keys, in this order. Used by the card inspector to show
   * just the knobs the selected view actually exposes. */
  only,
  className,
}: {
  schema: ConfigSchema | null | undefined;
  values: Record<string, unknown>;
  onChange: (key: string, value: unknown) => void;
  disabled?: boolean;
  density?: SchemaFormDensity;
  only?: readonly string[];
  className?: string;
}) {
  const all = schema?.properties ?? {};
  const properties = only
    ? Object.fromEntries(only.filter((k) => k in all).map((k) => [k, all[k]]))
    : all;
  const groups = groupProperties(properties);
  if (groups.length === 0) return null;

  const compact = density === "compact";
  return (
    <div className={cn(compact ? "space-y-3.5" : "space-y-6", className)}>
      {groups.map(([group, fields]) => (
        <div key={group} className={compact ? "space-y-3" : "space-y-4"}>
          {group && (
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              {group}
            </p>
          )}
          {fields.map(([key, prop]) => (
            <SchemaField
              key={key}
              fieldKey={key}
              prop={prop}
              value={values[key]}
              onChange={(v) => onChange(key, v)}
              disabled={disabled}
              density={density}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

/** Which control a field gets. Explicit `ui.widget` wins; otherwise inferred. */
function controlFor(prop: PropSchema): string {
  const explicit = prop.ui?.widget;
  const numeric = prop.type === "number" || prop.type === "integer";
  const hasEnum = Array.isArray(prop.enum) && prop.enum.length > 0;

  if (explicit === "slider" && numeric) return "slider";
  if (explicit === "segmented" && hasEnum) return "segmented";
  if (explicit === "multiselect" && hasEnum) return "multiselect";
  if (explicit === "select" && hasEnum) return "select";
  if (explicit === "switch" || prop.type === "boolean") return "switch";
  if (prop.type === "array" && hasEnum) return "multiselect";
  if (hasEnum) return "select";
  if (numeric) return "number";
  return "text";
}

export function SchemaField({
  fieldKey,
  prop,
  value,
  onChange,
  disabled = false,
  density = "comfortable",
}: {
  fieldKey: string;
  prop: PropSchema;
  value: unknown;
  onChange: (value: unknown) => void;
  disabled?: boolean;
  density?: SchemaFormDensity;
}) {
  const control = controlFor(prop);
  const compact = density === "compact";
  const label = prop.title ?? fieldKey;
  const id = `cfg-${fieldKey}`;
  const current = value ?? prop.default;
  const inputCls = compact ? "h-8 text-xs" : "";

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <Label htmlFor={id} className={cn("flex items-center gap-1", compact && "text-xs")}>
          {label}
          {prop.ui?.help && <InfoHint label={`About ${label}`}>{prop.ui.help}</InfoHint>}
        </Label>
        {/* A slider with no read-out is unusable — you can see the handle move
            but not what you set it to. */}
        {control === "slider" && (
          <span className="text-[11px] tabular-nums text-muted-foreground">
            {formatNumeric(current, prop)}
          </span>
        )}
      </div>

      {prop.description && !compact && (
        <p className="text-xs text-muted-foreground">{prop.description}</p>
      )}

      {control === "select" && (
        <Select
          value={String(current ?? "")}
          onValueChange={onChange}
          disabled={disabled}
        >
          <SelectTrigger id={id} className={cn(compact ? "h-8 text-xs" : "w-48")}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {prop.enum?.map((opt, i) => (
              <SelectItem key={opt} value={opt} className={compact ? "text-xs" : undefined}>
                {prop.enumLabels?.[i] ?? opt}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}

      {control === "segmented" && (
        <div className="flex flex-wrap gap-1" role="group" aria-labelledby={id}>
          {prop.enum?.map((opt, i) => {
            const active = String(current ?? "") === opt;
            return (
              <button
                key={opt}
                type="button"
                disabled={disabled}
                aria-pressed={active}
                onClick={() => onChange(opt)}
                className={cn(
                  "rounded-md border px-2 py-1 text-xs transition-colors",
                  active
                    ? "border-primary/40 bg-primary/10 text-foreground"
                    : "border-border text-muted-foreground hover:bg-muted/50",
                  disabled && "cursor-not-allowed opacity-50",
                )}
              >
                {prop.enumLabels?.[i] ?? opt}
              </button>
            );
          })}
        </div>
      )}

      {control === "multiselect" && (
        <div className="flex flex-wrap gap-1" role="group" aria-labelledby={id}>
          {prop.enum?.map((opt, i) => {
            const selected = Array.isArray(current) ? (current as unknown[]).includes(opt) : false;
            return (
              <button
                key={opt}
                type="button"
                disabled={disabled}
                aria-pressed={selected}
                onClick={() => {
                  const list = Array.isArray(current) ? [...(current as unknown[])] : [];
                  const at = list.indexOf(opt);
                  if (at >= 0) list.splice(at, 1);
                  else list.push(opt);
                  onChange(list);
                }}
                className={cn(
                  "rounded-md border px-2 py-1 text-xs transition-colors",
                  selected
                    ? "border-primary/40 bg-primary/10 text-foreground"
                    : "border-border text-muted-foreground hover:bg-muted/50",
                  disabled && "cursor-not-allowed opacity-50",
                )}
              >
                {prop.enumLabels?.[i] ?? opt}
              </button>
            );
          })}
        </div>
      )}

      {control === "switch" && (
        <Switch
          id={id}
          checked={Boolean(current)}
          onCheckedChange={onChange}
          disabled={disabled}
        />
      )}

      {control === "slider" && (
        <Slider
          id={id}
          min={prop.minimum ?? 0}
          max={prop.maximum ?? 100}
          step={prop.step ?? 1}
          value={[Number(current ?? prop.minimum ?? 0)]}
          onValueChange={([v]) => onChange(v)}
          disabled={disabled}
        />
      )}

      {control === "number" && (
        <Input
          id={id}
          type="number"
          min={prop.minimum}
          max={prop.maximum}
          step={prop.step}
          value={current == null ? "" : String(current)}
          onChange={(e) => onChange(e.target.value === "" ? undefined : Number(e.target.value))}
          disabled={disabled}
          className={inputCls}
        />
      )}

      {control === "text" && (
        <Input
          id={id}
          value={current == null ? "" : String(current)}
          onChange={(e) => onChange(e.target.value)}
          placeholder={String(prop.default ?? "")}
          disabled={disabled}
          className={cn(inputCls, !compact && "max-w-lg font-mono text-xs")}
        />
      )}

      {prop.description && compact && (
        <p className="text-[11px] leading-snug text-muted-foreground">{prop.description}</p>
      )}
    </div>
  );
}

/** Slider read-out, at the precision the step implies (0.05 -> "0.65"). */
function formatNumeric(value: unknown, prop: PropSchema): string {
  const n = Number(value ?? prop.minimum ?? 0);
  if (!Number.isFinite(n)) return "—";
  const step = prop.step ?? 1;
  if (step >= 1) return String(Math.round(n));
  return n.toFixed(String(step).split(".")[1]?.length ?? 2);
}
