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

export interface PropSchema {
  type?: string;
  title?: string;
  description?: string;
  default?: unknown;
  minimum?: number;
  maximum?: number;
  step?: number;
  enum?: string[];
  ui?: { widget?: string; group?: string };
}

export interface ConfigSchema {
  properties?: Record<string, PropSchema>;
}

/** Renders a module's JSON-schema config as a form, grouped by `ui.group`.
 *  Extracted from the module detail page so the admin Controls editor and the
 *  user read-only view share one renderer. `disabled` makes every input
 *  read-only (admin-locked / user view). */
export function ModuleConfigForm({
  properties,
  values,
  onChange,
  disabled = false,
}: {
  properties: Record<string, PropSchema>;
  values: Record<string, unknown>;
  onChange: (key: string, value: unknown) => void;
  disabled?: boolean;
}) {
  const groups = new Map<string, [string, PropSchema][]>();
  for (const [key, prop] of Object.entries(properties)) {
    const group = prop.ui?.group ?? "";
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group)!.push([key, prop]);
  }

  return (
    <div className="space-y-6">
      {Array.from(groups.entries()).map(([group, fields]) => (
        <div key={group} className="space-y-4">
          {group && (
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              {group}
            </p>
          )}
          {fields.map(([key, prop]) => (
            <ConfigField
              key={key}
              fieldKey={key}
              prop={prop}
              value={values[key] ?? prop.default ?? ""}
              onChange={(v) => onChange(key, v)}
              disabled={disabled}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

function ConfigField({
  fieldKey,
  prop,
  value,
  onChange,
  disabled,
}: {
  fieldKey: string;
  prop: PropSchema;
  value: unknown;
  onChange: (v: unknown) => void;
  disabled: boolean;
}) {
  const widget = prop.ui?.widget;
  const isSelect = widget === "select" || (prop.enum && prop.enum.length > 0);
  const isSlider = widget === "slider" && prop.type === "number";

  return (
    <div className="space-y-1.5">
      <Label htmlFor={fieldKey} className="text-sm">
        {prop.title ?? fieldKey}
      </Label>
      {prop.description && (
        <p className="text-xs text-muted-foreground">{prop.description}</p>
      )}

      {isSelect && prop.enum ? (
        <Select value={String(value)} onValueChange={(v) => onChange(v)} disabled={disabled}>
          <SelectTrigger id={fieldKey} className="w-48">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {prop.enum.map((opt) => (
              <SelectItem key={opt} value={opt}>
                {opt}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : isSlider ? (
        <div className="flex max-w-xl items-center gap-3 pt-1">
          <Slider
            id={fieldKey}
            min={prop.minimum ?? 0}
            max={prop.maximum ?? 1}
            step={prop.step ?? 0.01}
            value={[Number(value)]}
            onValueChange={([v]) => onChange(v)}
            disabled={disabled}
            className="flex-1"
          />
          <span className="tabular-nums text-sm text-muted-foreground w-12 shrink-0">
            {Number(value).toFixed(
              (prop.step ?? 1) < 1
                ? String(prop.step ?? 0.01).split(".")[1]?.length ?? 2
                : 0,
            )}
          </span>
        </div>
      ) : (
        <Input
          id={fieldKey}
          value={String(value)}
          onChange={(e) => onChange(e.target.value)}
          placeholder={String(prop.default ?? "")}
          disabled={disabled}
          className="max-w-lg font-mono text-xs"
        />
      )}
    </div>
  );
}
