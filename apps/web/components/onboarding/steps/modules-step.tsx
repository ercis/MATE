"use client";

import { useEffect, useState } from "react";
import { FileBox } from "lucide-react";
import { toastError } from "@/lib/toast";

import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { EmptyState } from "@/components/empty-state";
import {
  useModuleConfig,
  useModules,
  useUpdateModuleConfig,
} from "@/lib/queries";
import type { ModuleSummary } from "@/lib/api-types";

export function ModulesStep() {
  const { data: modules, isLoading } = useModules(null);

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-6">
      <div className="space-y-2 text-center">
        <h1 className="text-3xl font-semibold tracking-tight">Choose your modules</h1>
        <p className="text-sm text-muted-foreground">
          Enable the modules you want available. You can change this any time from Settings.
        </p>
      </div>

      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </div>
      ) : !modules || modules.length === 0 ? (
        <EmptyState
          icon={FileBox}
          title="No modules installed"
          description="Install modules from Settings → Modules after setup."
        />
      ) : (
        <div className="max-h-[60vh] space-y-2 overflow-y-auto pr-1">
          {modules.map((m) => (
            <ModuleToggleRow key={m.id} module={m} />
          ))}
        </div>
      )}
    </div>
  );
}

function ModuleToggleRow({ module: m }: { module: ModuleSummary }) {
  const { data: cfg } = useModuleConfig(m.id);
  const update = useUpdateModuleConfig();
  const [enabled, setEnabled] = useState(m.enabled);

  useEffect(() => {
    if (cfg !== undefined) setEnabled(cfg.enabled);
  }, [cfg]);

  const onToggle = async (val: boolean) => {
    const prev = enabled;
    setEnabled(val);
    try {
      await update.mutateAsync({
        id: m.id,
        config: cfg?.config ?? {},
        enabled: val,
      });
    } catch {
      setEnabled(prev);
      toastError(`Failed to update ${m.name}`);
    }
  };

  return (
    <div className="flex items-center justify-between gap-4 rounded-lg border border-border bg-surface px-4 py-3">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium">{m.name}</span>
          <Badge
            variant="secondary"
            className="h-5 px-2 py-0 text-[9px] font-medium uppercase tracking-wide"
          >
            {m.category.replace(/_/g, " ")}
          </Badge>
        </div>
        {m.description && (
          <p className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">{m.description}</p>
        )}
      </div>
      <Switch
        checked={enabled}
        onCheckedChange={onToggle}
        disabled={update.isPending}
        aria-label={`Toggle ${m.name}`}
      />
    </div>
  );
}
