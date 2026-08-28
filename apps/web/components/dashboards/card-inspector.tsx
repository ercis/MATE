"use client";

import { ArrowUpRight, MousePointerSquareDashed, Trash2 } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/cn";
import { SchemaForm } from "@/components/schema-form/schema-form";
import { WidgetHelpBody, hasHelp } from "@/components/dashboards/kit/help";
import { cardIcon } from "@/components/dashboards/card-icon";
import { drillLabel, resolveDrillHref } from "@/lib/dashboards/drill";
import type { CardPatch } from "@/components/dashboards/dashboard-card";
import type { DashboardCard, DashboardItem } from "@/lib/dashboard-queries";

/** Config key holding the KPI subset a placement shows. Read by widgets that
 * declare `kpis:` in their manifest. */
const KPIS_CONFIG_KEY = "kpis";

/**
 * Settings for the selected card(s).
 *
 * Replaces the 288px popover hung off a 22px header icon. That popover was
 * fine for one `top_n` slider and hopeless for what a card should actually
 * expose — the module's real views and knobs, a KPI subset, its layout — which
 * is why cards ended up being stripped-down versions of their modules.
 *
 * Rendered as an OVERLAY, not a flex sibling: as a sibling it would change the
 * canvas's measured width every time it opened and move every card, which is
 * precisely the bug the palette was just converted away from.
 */
export function CardInspector({
  items,
  selectedIds,
  catalog,
  logId,
  onUpdate,
  onRemove,
  onClose,
}: {
  items: DashboardItem[];
  selectedIds: readonly string[];
  catalog: DashboardCard[] | undefined;
  logId: string | null;
  onUpdate: (id: string, patch: CardPatch) => void;
  onRemove: (ids: string[]) => void;
  onClose: () => void;
}) {
  const selected = items.filter((it) => selectedIds.includes(it.i));

  return (
    <aside className="flex h-full w-72 flex-col border-l border-border bg-muted/20">
      {selected.length === 0 ? (
        <Empty />
      ) : selected.length === 1 ? (
        <SingleCard
          item={selected[0]}
          catalog={catalog}
          logId={logId}
          onUpdate={(patch) => onUpdate(selected[0].i, patch)}
          onRemove={() => onRemove([selected[0].i])}
        />
      ) : (
        <MultiSelection selected={selected} onRemove={() => onRemove(selected.map((s) => s.i))} />
      )}
      <button type="button" className="sr-only" onClick={onClose}>
        Close inspector
      </button>
    </aside>
  );
}

function Empty() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center">
      <MousePointerSquareDashed className="h-5 w-5 text-muted-foreground/60" aria-hidden />
      <p className="text-xs text-muted-foreground">
        Select a card to configure it. Shift-click to select several.
      </p>
    </div>
  );
}

function SingleCard({
  item,
  catalog,
  logId,
  onUpdate,
  onRemove,
}: {
  item: DashboardItem;
  catalog: DashboardCard[] | undefined;
  logId: string | null;
  onUpdate: (patch: CardPatch) => void;
  onRemove: () => void;
}) {
  const meta = catalog?.find(
    (c) => c.module_id === item.module_id && c.widget_id === item.widget_id,
  );
  const Icon = cardIcon(meta?.icon ?? null);
  const title = item.title || meta?.title || item.widget_id || "Card";

  // Which of the module's views this card renders. A widget declaring several
  // is the mechanism that stops a card being pinned to one hardcoded slice of
  // its module.
  const views = meta?.views ?? [];
  const activeViewId =
    typeof item.config.view === "string" ? item.config.view : (views[0]?.id ?? null);
  const activeView = views.find((v) => v.id === activeViewId);
  // A view narrows the visible knobs to the ones that apply to it.
  const exposed = activeView?.exposes?.length ? activeView.exposes : undefined;

  const kpis = meta?.kpis ?? [];
  const selectedKpis: string[] = Array.isArray(item.config[KPIS_CONFIG_KEY])
    ? (item.config[KPIS_CONFIG_KEY] as string[])
    : kpis.filter((k) => k.default !== false).map((k) => k.id);

  const drillHref =
    item.kind === "viz" || !item.module_id
      ? null
      : resolveDrillHref({ moduleId: item.module_id, logId, manifestDrill: meta?.drill });

  const setConfig = (key: string, value: unknown) =>
    onUpdate({ config: { ...item.config, [key]: value } });

  return (
    <>
      <header className="flex shrink-0 items-start gap-2 border-b border-border p-3">
        <Icon className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs font-semibold tracking-tight">{title}</p>
          {meta?.module_name && (
            <p className="truncate text-[11px] text-muted-foreground">{meta.module_name}</p>
          )}
        </div>
        {drillHref && (
          <Button asChild variant="ghost" size="icon" className="h-6 w-6 shrink-0">
            <Link href={drillHref} aria-label={drillLabel(meta?.drill)}>
              <ArrowUpRight className="h-3.5 w-3.5" />
            </Link>
          </Button>
        )}
      </header>

      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-4 p-3">
          <Section title="Card">
            <div className="space-y-1.5">
              <Label htmlFor="inspector-title" className="text-xs">
                Title
              </Label>
              <Input
                id="inspector-title"
                value={item.title ?? ""}
                onChange={(e) => onUpdate({ title: e.target.value })}
                placeholder={meta?.title ?? item.widget_id}
                className="h-8 text-xs"
              />
            </div>

            {views.length > 1 && (
              <div className="space-y-1.5">
                <Label className="text-xs">View</Label>
                <div className="flex flex-wrap gap-1">
                  {views.map((v) => (
                    <button
                      key={v.id}
                      type="button"
                      aria-pressed={v.id === activeViewId}
                      onClick={() => setConfig("view", v.id)}
                      title={v.description ?? undefined}
                      className={cn(
                        "rounded-md border px-2 py-1 text-xs transition-colors",
                        v.id === activeViewId
                          ? "border-primary/40 bg-primary/10 text-foreground"
                          : "border-border text-muted-foreground hover:bg-muted/50",
                      )}
                    >
                      {v.title}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </Section>

          {kpis.length > 0 && (
            <Section
              title="Figures"
              hint="Choose what this card shows. Fewer figures let it sit smaller."
            >
              <div className="space-y-1">
                {kpis.map((k) => {
                  const on = selectedKpis.includes(k.id);
                  return (
                    <label
                      key={k.id}
                      className="flex cursor-pointer items-start gap-2 rounded-md px-1.5 py-1 text-xs hover:bg-muted/50"
                    >
                      <input
                        type="checkbox"
                        checked={on}
                        onChange={() =>
                          setConfig(
                            KPIS_CONFIG_KEY,
                            on
                              ? selectedKpis.filter((id) => id !== k.id)
                              : [...selectedKpis, k.id],
                          )
                        }
                        className="mt-0.5 h-3 w-3 shrink-0"
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate">{k.title}</span>
                        {k.info && (
                          <span className="mt-0.5 block text-[11px] leading-snug text-muted-foreground">
                            {k.info}
                          </span>
                        )}
                      </span>
                    </label>
                  );
                })}
              </div>
            </Section>
          )}

          {Object.keys(meta?.config_schema?.properties ?? {}).length > 0 && (
            <Section title="Options">
              <SchemaForm
                schema={meta?.config_schema}
                values={item.config}
                onChange={setConfig}
                density="compact"
                only={exposed}
              />
            </Section>
          )}

          <Section title="Layout">
            <div className="grid grid-cols-2 gap-2">
              <NumberField label="X" value={item.x} onChange={(x) => onUpdate({ x } as CardPatch)} />
              <NumberField label="Y" value={item.y} onChange={(y) => onUpdate({ y } as CardPatch)} />
              <NumberField label="W" value={item.w} onChange={(w) => onUpdate({ w } as CardPatch)} />
              <NumberField label="H" value={item.h} onChange={(h) => onUpdate({ h } as CardPatch)} />
            </div>
            {meta && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="w-full"
                onClick={() =>
                  onUpdate({ w: meta.default_w, h: meta.default_h } as CardPatch)
                }
              >
                Reset to default size
              </Button>
            )}
          </Section>

          {hasHelp(meta?.help, meta?.description) && (
            <Section title="About">
              <WidgetHelpBody help={meta?.help} fallback={meta?.description} />
            </Section>
          )}
        </div>
      </ScrollArea>

      <div className="shrink-0 border-t border-border p-3">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="w-full text-muted-foreground hover:text-destructive"
          onClick={onRemove}
        >
          <Trash2 className="mr-1.5 h-3.5 w-3.5" />
          Remove card
        </Button>
      </div>
    </>
  );
}

function MultiSelection({
  selected,
  onRemove,
}: {
  selected: DashboardItem[];
  onRemove: () => void;
}) {
  return (
    <>
      <header className="shrink-0 border-b border-border p-3">
        <p className="text-xs font-semibold tracking-tight">{selected.length} cards selected</p>
        <p className="mt-0.5 text-[11px] text-muted-foreground">
          Arrow keys nudge · ⌘D duplicates · Delete removes
        </p>
      </header>
      <div className="min-h-0 flex-1 p-3">
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          Per-card options apply to one card at a time. Select a single card to change what it
          shows.
        </p>
      </div>
      <div className="shrink-0 border-t border-border p-3">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="w-full text-muted-foreground hover:text-destructive"
          onClick={onRemove}
        >
          <Trash2 className="mr-1.5 h-3.5 w-3.5" />
          Remove {selected.length} cards
        </Button>
      </div>
    </>
  );
}

function Section({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-2">
      <div>
        <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
          {title}
        </p>
        {hint && <p className="mt-0.5 text-[11px] text-muted-foreground">{hint}</p>}
      </div>
      {children}
      <Separator className="mt-3" />
    </section>
  );
}

function NumberField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <div className="space-y-1">
      <Label className="text-[11px] text-muted-foreground">{label}</Label>
      <Input
        type="number"
        value={value}
        onChange={(e) => {
          const n = Number(e.target.value);
          if (Number.isFinite(n)) onChange(n);
        }}
        className="h-8 text-xs"
      />
    </div>
  );
}
