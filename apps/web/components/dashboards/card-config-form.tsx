"use client";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type {
  DashboardItem,
  WidgetConfigSchema,
  WidgetPropSchema,
} from "@/lib/dashboard-queries";

/**
 * Settings form for one placed card, rendered in the card's edit-mode popover.
 * Always offers a Title field; the rest is generated from the widget's
 * `config_schema` (the same JSON-Schema dialect modules use for their own
 * settings) – select / slider / number / switch / text. Changes are applied
 * live so the user sees the widget update as they tweak.
 */
export function CardConfigForm({
  item,
  schema,
  onChange,
}: {
  item: DashboardItem;
  schema: WidgetConfigSchema | null | undefined;
  onChange: (patch: { title?: string; config?: Record<string, unknown> }) => void;
}) {
  const props = Object.entries(schema?.properties ?? {});

  const setConfig = (key: string, value: unknown) =>
    onChange({ config: { ...item.config, [key]: value } });

  return (
    <div className="space-y-3.5">
      <div className="space-y-1.5">
        <Label htmlFor="card-title" className="text-xs">
          Title
        </Label>
        <Input
          id="card-title"
          value={item.title ?? ""}
          onChange={(e) => onChange({ title: e.target.value })}
          placeholder={item.widget_id}
          className="h-8 text-xs"
        />
      </div>

      {props.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">
          This card has no other options.
        </p>
      ) : (
        props.map(([key, prop]) => (
          <Field
            key={key}
            fieldKey={key}
            prop={prop}
            value={item.config[key]}
            onChange={(v) => setConfig(key, v)}
          />
        ))
      )}
    </div>
  );
}

function Field({
  fieldKey,
  prop,
  value,
  onChange,
}: {
  fieldKey: string;
  prop: WidgetPropSchema;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const widget = prop.ui?.widget;
  const isSelect = widget === "select" || (prop.enum != null && prop.enum.length > 0);
  const isNumeric = prop.type === "number" || prop.type === "integer";
  const isSlider = widget === "slider" && isNumeric;
  const isSwitch = prop.type === "boolean";
  const label = prop.title ?? fieldKey;

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <Label htmlFor={`cfg-${fieldKey}`} className="text-xs">
          {label}
        </Label>
        {isSlider && (
          <span className="text-[11px] tabular-nums text-muted-foreground">
            {Number(value ?? prop.default ?? prop.minimum ?? 0)}
          </span>
        )}
      </div>

      {isSelect && prop.enum ? (
        <Select value={String(value ?? prop.default ?? "")} onValueChange={onChange}>
          <SelectTrigger id={`cfg-${fieldKey}`} className="h-8 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {prop.enum.map((opt, i) => (
              <SelectItem key={opt} value={opt} className="text-xs">
                {prop.enumLabels?.[i] ?? opt}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : isSwitch ? (
        <Switch
          id={`cfg-${fieldKey}`}
          checked={Boolean(value ?? prop.default)}
          onCheckedChange={onChange}
        />
      ) : isSlider ? (
        <Slider
          id={`cfg-${fieldKey}`}
          min={prop.minimum ?? 0}
          max={prop.maximum ?? 100}
          step={prop.step ?? 1}
          value={[Number(value ?? prop.default ?? prop.minimum ?? 0)]}
          onValueChange={(vals) => onChange(vals[0])}
        />
      ) : isNumeric ? (
        <Input
          id={`cfg-${fieldKey}`}
          type="number"
          min={prop.minimum}
          max={prop.maximum}
          step={prop.step}
          value={value == null ? "" : String(value)}
          onChange={(e) => onChange(e.target.value === "" ? undefined : Number(e.target.value))}
          className="h-8 text-xs"
        />
      ) : (
        <Input
          id={`cfg-${fieldKey}`}
          value={value == null ? "" : String(value)}
          onChange={(e) => onChange(e.target.value)}
          className="h-8 text-xs"
        />
      )}

      {prop.description && (
        <p className="text-[11px] leading-snug text-muted-foreground">{prop.description}</p>
      )}
    </div>
  );
}
