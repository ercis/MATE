"use client";

import { useMemo } from "react";

import Link from "next/link";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { Skeleton } from "@/components/ui/skeleton";
import {
  PageContainer,
  PageHeader,
  PageTitle,
  PageDescription,
} from "@/components/page";
import { EmptyState } from "@/components/empty-state";
import { FileBox, Plus, RotateCcw } from "lucide-react";
import { toastError } from "@/lib/toast";
import {
  useModules,
  useModuleConfig,
  useRestoreDefaults,
  useUpdateModuleConfig,
} from "@/lib/queries";
import type { ModuleSummary } from "@/lib/api-types";

// Section order on the page; unknown categories are appended alphabetically.
const CATEGORY_ORDER = [
  "foundation",
  "attribute",
  "external_input",
  "advanced",
  "comparison",
  "other",
];

const CATEGORY_LABELS: Record<string, string> = {
  foundation: "Foundation",
  attribute: "Attribute",
  external_input: "External input",
  advanced: "Advanced",
  comparison: "Comparison",
  other: "Other",
};

function categoryLabel(cat: string): string {
  return CATEGORY_LABELS[cat] ?? cat.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Group modules by `category`, ordered by `CATEGORY_ORDER` with any unknown
 *  category appended alphabetically. */
function groupByCategory(modules: ModuleSummary[]): [string, ModuleSummary[]][] {
  const map = new Map<string, ModuleSummary[]>();
  for (const m of modules) {
    const list = map.get(m.category);
    if (list) list.push(m);
    else map.set(m.category, [m]);
  }
  const ordered = [...map.keys()].sort((a, b) => {
    const ia = CATEGORY_ORDER.indexOf(a);
    const ib = CATEGORY_ORDER.indexOf(b);
    if (ia !== -1 && ib !== -1) return ia - ib;
    if (ia !== -1) return -1;
    if (ib !== -1) return 1;
    return a.localeCompare(b);
  });
  return ordered.map((cat) => [cat, map.get(cat)!]);
}

function ModuleActions() {
  const restore = useRestoreDefaults();

  const onRestore = async () => {
    try {
      const res = await restore.mutateAsync();
      if (res.restored.length === 0) {
        toast.success("All default modules are already installed");
      } else {
        toast.success(`Restored ${res.restored.length} default module(s)`);
      }
    } catch (err: unknown) {
      toastError(`Restore failed: ${(err as Error).message}`);
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button
        variant="outline"
        size="sm"
        className="cursor-pointer gap-1.5"
        onClick={onRestore}
        disabled={restore.isPending}
      >
        <RotateCcw className="h-3.5 w-3.5" />
        {restore.isPending ? "Restoring…" : "Restore defaults"}
      </Button>
      <Button asChild size="sm" className="cursor-pointer">
        <Link href="/modules/import">
          <Plus className="mr-1.5 h-3.5 w-3.5" />
          Install a module
        </Link>
      </Button>
    </div>
  );
}

function ModuleCard({ m }: { m: ModuleSummary }) {
  // The PUT replaces the stored config wholesale, so we must read the saved
  // config first and hand it back unchanged when flipping `enabled` – toggling
  // with an empty config would wipe the module's settings. The switch stays
  // disabled until the config loads to keep that invariant.
  const { data: cfg } = useModuleConfig(m.id);
  const update = useUpdateModuleConfig();
  const enabled = cfg?.enabled ?? m.enabled;

  const onToggle = async (val: boolean) => {
    if (!cfg) return;
    try {
      await update.mutateAsync({ id: m.id, config: cfg.config, enabled: val });
      toast.success(val ? `${m.name} enabled` : `${m.name} disabled`);
    } catch {
      toastError("Failed to update module");
    }
  };

  return (
    <Card className="gap-0 py-0">
      <CardContent className="space-y-3 p-4">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h3 className="truncate text-sm font-semibold">{m.name}</h3>
              <span className="text-xs text-muted-foreground">{m.version}</span>
            </div>
            {m.author && (
              <p className="mt-0.5 truncate text-xs text-muted-foreground">by {m.author}</p>
            )}
          </div>
          <Switch
            checked={enabled}
            onCheckedChange={onToggle}
            disabled={cfg === undefined || update.isPending}
            aria-label={enabled ? `Disable ${m.name}` : `Enable ${m.name}`}
            className="cursor-pointer shrink-0"
          />
        </div>
        {m.description && (
          <p className="line-clamp-2 text-xs text-muted-foreground">{m.description}</p>
        )}
        <Button asChild variant="outline" size="sm" className="cursor-pointer w-full">
          <Link href={`/modules/${m.id}`}>Configure</Link>
        </Button>
      </CardContent>
    </Card>
  );
}

export function ModulesClient() {
  const { data: modules, isLoading } = useModules(null);
  const groups = useMemo(() => groupByCategory(modules ?? []), [modules]);

  return (
    <PageContainer>
      <PageHeader>
        <div className="space-y-1">
          <PageTitle>Modules</PageTitle>
          <PageDescription>
            Enable or disable modules, or open one to configure it.
          </PageDescription>
        </div>
        <ModuleActions />
      </PageHeader>

      {isLoading ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-32" />
          ))}
        </div>
      ) : !modules || modules.length === 0 ? (
        <EmptyState
          icon={FileBox}
          title="No modules installed"
          description="Restore the defaults above, or upload your own .zip / .tar.gz."
        />
      ) : (
        <div className="space-y-8">
          {groups.map(([cat, mods]) => (
            <section key={cat} className="space-y-3">
              <div className="flex items-baseline gap-2">
                <h2 className="text-sm font-semibold tracking-tight">{categoryLabel(cat)}</h2>
                <span className="text-xs text-muted-foreground">{mods.length}</span>
              </div>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {mods.map((m) => (
                  <ModuleCard key={m.id} m={m} />
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </PageContainer>
  );
}
