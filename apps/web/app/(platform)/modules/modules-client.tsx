"use client";

import { useMemo } from "react";

import Link from "next/link";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { TableSkeleton } from "@/components/skeletons";
import { PageContainer, PageHeader } from "@/components/page";
import { EmptyState } from "@/components/empty-state";
import { FileBox, Lock, Plus, RotateCcw } from "lucide-react";
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
  foundation: "Process Discovery",
  attribute: "Attribute Analysis",
  external_input: "External Data",
  advanced: "Process Intelligence",
  comparison: "Process Comparison",
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

function ModuleRow({ m }: { m: ModuleSummary }) {
  // The PUT replaces the stored config wholesale, so we must read the saved
  // config first and hand it back unchanged when flipping `enabled` – toggling
  // with an empty config would wipe the module's settings. The switch stays
  // disabled until the config loads to keep that invariant.
  const { data: cfg } = useModuleConfig(m.id);
  const update = useUpdateModuleConfig();
  const enabled = cfg?.enabled ?? m.enabled;
  // Admin locked this module's config platform-wide: badge it and freeze the toggle.
  const controlled = cfg?.controlled_by_admin ?? false;

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
    <div className="flex items-center gap-4 px-4 py-3">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="truncate text-sm font-semibold">{m.name}</h3>
          <span className="text-xs text-muted-foreground">{m.version}</span>
          {controlled && (
            <Badge
              variant="outline"
              className="h-5 gap-1 border-destructive/30 bg-destructive/10 px-1.5 py-0 text-[10px] text-destructive"
            >
              <Lock className="h-3 w-3" />
              Admin-controlled
            </Badge>
          )}
        </div>
        {m.description && (
          <p className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">{m.description}</p>
        )}
      </div>
      <Button asChild variant="outline" size="sm" className="shrink-0 cursor-pointer">
        <Link href={`/modules/${m.id}`}>Configure</Link>
      </Button>
      <Switch
        checked={enabled}
        onCheckedChange={onToggle}
        disabled={cfg === undefined || update.isPending || controlled}
        aria-label={enabled ? `Disable ${m.name}` : `Enable ${m.name}`}
        className={controlled ? "cursor-pointer shrink-0 opacity-60" : "cursor-pointer shrink-0"}
      />
    </div>
  );
}

export function ModulesClient() {
  const { data: modules, isLoading } = useModules(null);
  const groups = useMemo(() => groupByCategory(modules ?? []), [modules]);

  return (
    <PageContainer>
      <PageHeader className="justify-end">
        <ModuleActions />
      </PageHeader>

      {isLoading ? (
        <TableSkeleton rows={9} />
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
              <Card className="gap-0 py-0">
                <CardContent className="divide-y divide-border p-0">
                  {mods.map((m) => (
                    <ModuleRow key={m.id} m={m} />
                  ))}
                </CardContent>
              </Card>
            </section>
          ))}
        </div>
      )}
    </PageContainer>
  );
}
