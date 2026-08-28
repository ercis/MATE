"use client";

import { Gauge, Layers, LayoutTemplate, Loader2, Workflow, type LucideIcon } from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/cn";
import { useDashboardTemplates, type DashboardTemplate } from "@/lib/dashboard-queries";

/** Per-template accent icon, keyed by the server template id (falls back to a
 * generic template glyph for any future template). */
const TEMPLATE_ICON: Record<string, LucideIcon> = {
  "process-overview": Workflow,
  performance: Gauge,
  complexity: Layers,
};

export function TemplatePicker({
  open,
  onOpenChange,
  onSelect,
  pendingId,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Instantiate a board from this template, then navigate to it. */
  onSelect: (template: DashboardTemplate) => void;
  /** The template currently being created (spinner + disabled), if any. */
  pendingId: string | null;
}) {
  const { data: templates, isLoading } = useDashboardTemplates();
  const busy = pendingId !== null;

  return (
    <Dialog open={open} onOpenChange={(o) => !busy && onOpenChange(o)}>
      <DialogContent className="sm:max-w-lg" aria-describedby={undefined}>
        <DialogHeader>
          <DialogTitle>Start from a template</DialogTitle>
        </DialogHeader>

        <div className="grid gap-2">
          {isLoading ? (
            [0, 1, 2].map((i) => <Skeleton key={i} className="h-[4.5rem] w-full" />)
          ) : !templates || templates.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              No templates available.
            </p>
          ) : (
            templates.map((t) => {
              const Icon = TEMPLATE_ICON[t.id] ?? LayoutTemplate;
              const isPending = pendingId === t.id;
              return (
                <button
                  key={t.id}
                  type="button"
                  disabled={busy}
                  onClick={() => onSelect(t)}
                  className={cn(
                    "flex items-start gap-3 rounded-lg border border-border p-3 text-left transition-colors",
                    "hover:border-primary/40 hover:bg-muted/40 focus-visible:outline-none",
                    "focus-visible:ring-1 focus-visible:ring-primary disabled:pointer-events-none",
                    isPending ? "border-primary bg-primary/5" : "disabled:opacity-60",
                  )}
                >
                  <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
                    {isPending ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <Icon className="h-4 w-4" />
                    )}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center justify-between gap-2">
                      <span className="text-sm font-medium">{t.name}</span>
                      <span className="shrink-0 text-[11px] text-muted-foreground">
                        {t.card_count} card{t.card_count === 1 ? "" : "s"}
                      </span>
                    </span>
                    <span className="mt-0.5 block text-[11px] leading-snug text-muted-foreground">
                      {t.description}
                    </span>
                  </span>
                </button>
              );
            })
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
