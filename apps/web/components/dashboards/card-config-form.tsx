"use client";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SchemaForm } from "@/components/schema-form/schema-form";
import type { DashboardItem, WidgetConfigSchema } from "@/lib/dashboard-queries";

// The generic-viz field mapper renders one option at a time, so it needs the
// field-level renderer rather than the whole form.
export { SchemaField as Field } from "@/components/schema-form/schema-form";

/**
 * Settings for one placed card.
 *
 * Title, then the widget's declared options rendered by the shared
 * `SchemaForm` — the same renderer the module settings page uses, so a card's
 * options are no longer a lesser version of the module's (this file used to
 * carry its own copy, which supported different controls).
 *
 * The per-card *filter* editor that used to live here is gone. It was a
 * documented anti-pattern — a filter inside a chart card, when the board
 * already has one filter row scoping everything — and worse, it never worked:
 * the value was written into the placement's config and then silently ignored
 * for module widget cards, which fetch through their own hooks. It promised
 * "applies to this card only" and did nothing.
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
  const hasOptions = Object.keys(schema?.properties ?? {}).length > 0;

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

      {hasOptions ? (
        <SchemaForm
          schema={schema}
          values={item.config}
          onChange={(key, value) => onChange({ config: { ...item.config, [key]: value } })}
          density="compact"
        />
      ) : (
        <p className="text-[11px] text-muted-foreground">This card has no other options.</p>
      )}
    </div>
  );
}
