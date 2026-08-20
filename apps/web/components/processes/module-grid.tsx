"use client";

import Link from "next/link";
import { useMemo } from "react";
import { Plus, FileBox } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/empty-state";
import { ModuleCard } from "@/components/processes/module-card";
import { useModules } from "@/lib/queries";
import { useUi } from "@/lib/stores/ui";
import type { ModuleSummary } from "@/lib/api-types";

const CATEGORIES: { id: string; label: string }[] = [
  { id: "foundation", label: "Foundation" },
  { id: "attribute", label: "Attribute" },
  { id: "external_input", label: "External input" },
  { id: "advanced", label: "Advanced process analytics" },
  { id: "comparison", label: "Comparison" },
  { id: "other", label: "Other" },
];

export function ModuleGrid({ logId }: { logId: string }) {
  const { data: modules, isLoading, isError } = useModules(logId);
  const confidentialOnly = useUi((s) => s.confidentialOnly);

  const grouped = useMemo(() => {
    const out = new Map<string, ModuleSummary[]>();
    for (const c of CATEGORIES) out.set(c.id, []);
    for (const m of modules ?? []) {
      if (confidentialOnly && !m.is_confidential_safe) continue;
      // Only surface modules that can actually be opened for this log – hide
      // disabled ones and those the log is incompatible with (unavailable).
      if (m.enabled === false) continue;
      if ((m.availability?.status ?? "available") === "unavailable") continue;
      const bucket = out.get(m.category) ?? out.get("other")!;
      bucket.push(m);
    }
    return out;
  }, [modules, confidentialOnly]);

  const visibleCount = useMemo(
    () => [...grouped.values()].reduce((n, bucket) => n + bucket.length, 0),
    [grouped],
  );

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

  if (visibleCount === 0) {
    return (
      <EmptyState
        icon={FileBox}
        title="No available modules"
        description="None of your installed modules are compatible with this process. Import another module or adjust your installed modules."
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
              {bucket.map((m) => (
                <ModuleCard key={m.id} module={m} logId={logId} />
              ))}
            </div>
          </section>
        );
      })}
    </div>
  );
}

