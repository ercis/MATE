"use client";

import Link from "next/link";
import { useMemo } from "react";
import { Plus, FileBox, Search, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/empty-state";
import { DotPattern } from "@/components/glass/dot-pattern";
import { ModuleCard } from "@/components/processes/module-card";
import { useModules } from "@/lib/queries";
import { stagger } from "@/lib/stagger";
import { useUi } from "@/lib/stores/ui";
import { cn } from "@/lib/cn";
import type { ModuleSummary } from "@/lib/api-types";

const CATEGORIES: { id: string; label: string }[] = [
  { id: "foundation", label: "Process Discovery" },
  { id: "attribute", label: "Attribute Analysis" },
  { id: "external_input", label: "External Data" },
  { id: "advanced", label: "Process Intelligence" },
  { id: "comparison", label: "Process Comparison" },
  { id: "other", label: "Other" },
];

// Empty/error states sit on a plain card with a faint dot-pattern behind them.
function CardEmpty(props: React.ComponentProps<typeof EmptyState>) {
  return (
    <Card className="relative overflow-hidden py-0">
      <DotPattern
        className="text-muted-foreground/50 [mask-image:radial-gradient(ellipse_at_center,black,transparent_75%)]"
        dotOpacity={0.15}
      />
      <CardContent className="relative px-6">
        <EmptyState {...props} />
      </CardContent>
    </Card>
  );
}

// Module search. Rendered by the process detail view inside the tab row
// (Overview/Events/…), so it's a standalone controlled component; ModuleGrid
// consumes the same `query`.
export function ModuleSearchBar({
  query,
  onQueryChange,
  className,
}: {
  query: string;
  onQueryChange: (value: string) => void;
  className?: string;
}) {
  return (
    <div className={cn("relative w-44 sm:w-60", className)}>
      <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
      <Input
        value={query}
        onChange={(e) => onQueryChange(e.target.value)}
        placeholder="Search modules…"
        aria-label="Search modules"
        className="h-8 pl-8 pr-8"
      />
      {query && (
        <button
          type="button"
          onClick={() => onQueryChange("")}
          aria-label="Clear search"
          className="absolute right-2 top-1/2 -translate-y-1/2 cursor-pointer rounded p-0.5 text-muted-foreground transition-colors hover:text-foreground"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  );
}

export function ModuleGrid({
  logId,
  query,
  onQueryChange,
}: {
  logId: string;
  query: string;
  onQueryChange: (value: string) => void;
}) {
  const { data: modules, isLoading, isError } = useModules(logId);
  const confidentialOnly = useUi((s) => s.confidentialOnly);
  const showUnavailable = useUi((s) => s.showUnavailableModules);
  const showDisabled = useUi((s) => s.showDisabledModules);
  const q = query.trim().toLowerCase();

  const grouped = useMemo(() => {
    // A module the log doesn't qualify for (manifest `requirements` not met)
    // stays VISIBLE by default: ModuleCard renders it grayed-out with a tooltip
    // listing the unmet requirements. Hiding it made users think the module was
    // gone. `showUnavailableModules` / `showDisabledModules` (UI store; the
    // MATE AI settings tool flips them) let users opt out / in; the
    // confidential-mode filter always hides – it's a deliberate presentation
    // mode, not an applicability gate.
    const openable = (m: ModuleSummary) =>
      m.enabled !== false && (m.availability?.status ?? "available") !== "unavailable";
    const out = new Map<string, ModuleSummary[]>();
    for (const c of CATEGORIES) out.set(c.id, []);
    for (const m of modules ?? []) {
      if (q && !`${m.name} ${m.description ?? ""}`.toLowerCase().includes(q)) continue;
      if (confidentialOnly && !m.is_confidential_safe) continue;
      if (!showDisabled && m.enabled === false) continue;
      if (
        !showUnavailable &&
        m.enabled !== false &&
        (m.availability?.status ?? "available") === "unavailable"
      )
        continue;
      const bucket = out.get(m.category) ?? out.get("other")!;
      bucket.push(m);
    }
    // Openable modules first within each category; sort is stable, so the
    // manifest order is preserved inside each half.
    for (const bucket of out.values()) {
      bucket.sort((a, b) => Number(openable(b)) - Number(openable(a)));
    }
    return out;
  }, [modules, confidentialOnly, showUnavailable, showDisabled, q]);

  const visibleCount = useMemo(
    () => [...grouped.values()].reduce((n, bucket) => n + bucket.length, 0),
    [grouped],
  );

  // Running card index across category buckets, so the entrance stagger
  // cascades over the whole grid instead of restarting per section.
  const categoryOffsets = useMemo(() => {
    const offsets = new Map<string, number>();
    let n = 0;
    for (const c of CATEGORIES) {
      offsets.set(c.id, n);
      n += grouped.get(c.id)?.length ?? 0;
    }
    return offsets;
  }, [grouped]);

  if (isLoading) {
    return (
      <div className="space-y-5">
        {CATEGORIES.slice(0, 2).map((c) => (
          <section key={c.id} className="space-y-2.5">
            <div className="pb-1 border-b border-border">
              <h2 className="text-xs font-semibold uppercase tracking-wider text-foreground/70">
                {c.label}
              </h2>
            </div>
            <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-40" />
              ))}
            </div>
          </section>
        ))}
      </div>
    );
  }

  if (isError) {
    return (
      <EmptyState
        icon={FileBox}
        title="Couldn't load modules"
        description="The module loader is offline or failed to start. Check the API logs."
      />
    );
  }

  if (!modules || modules.length === 0) {
    return (
      <EmptyState
        icon={FileBox}
        title="No modules installed"
        description="v1 ships with no modules. Install one to enable analytics on this process."
        primaryAction={
          <Button asChild className="cursor-pointer gap-2">
            <Link href="/modules/import">
              <Plus className="h-4 w-4" />
              Import module
            </Link>
          </Button>
        }
      />
    );
  }

  return (
    <div data-tour="module-grid">
      {visibleCount === 0 ? (
        <CardEmpty
          icon={FileBox}
          title={
            q
              ? `No modules match “${query.trim()}”`
              : confidentialOnly
                ? "No confidential-safe modules"
                : "No modules to show"
          }
          description={
            q
              ? "No installed module matches your search. Try a different term or clear the filter."
              : confidentialOnly
                ? "Confidential mode is on and none of your installed modules are marked safe for it. Turn off confidential mode or import a confidential-safe module."
                : "All of your installed modules are disabled or incompatible with this process, and your view settings hide them. Import another module or adjust your installed modules."
          }
          primaryAction={
            q ? (
              <Button className="cursor-pointer gap-2" onClick={() => onQueryChange("")}>
                <X className="h-4 w-4" />
                Clear search
              </Button>
            ) : (
              <Button asChild className="cursor-pointer gap-2">
                <Link href="/modules/import">
                  <Plus className="h-4 w-4" />
                  Import module
                </Link>
              </Button>
            )
          }
        />
      ) : (
        <div className="space-y-5">
          {CATEGORIES.map((c) => {
            const bucket = grouped.get(c.id)!;
            if (bucket.length === 0) return null;
            return (
              <section key={c.id} className="space-y-2.5">
                <div className="flex items-center gap-2 pb-1 border-b border-border">
                  <h2 className="text-xs font-semibold uppercase tracking-wider text-foreground/70">
                    {c.label}
                  </h2>
                  <span className="text-[10px] text-muted-foreground/60">({bucket.length})</span>
                </div>
                <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                  {bucket.map((m, i) => (
                    // Entrance stagger lives on a wrapper so the card's own hover
                    // transform isn't pinned by the filled animation.
                    <div
                      key={m.id}
                      className="h-full animate-in fade-in-0 slide-in-from-bottom-1 fill-mode-both duration-300"
                      style={stagger((categoryOffsets.get(c.id) ?? 0) + i)}
                    >
                      <ModuleCard module={m} logId={logId} />
                    </div>
                  ))}
                </div>
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}

