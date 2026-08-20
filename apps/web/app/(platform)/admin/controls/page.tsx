"use client";

import { useEffect, useMemo, useState } from "react";
import { ChevronDown, Loader2, Lock, ShieldAlert, Unlock } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  ModuleConfigForm,
  type ConfigSchema,
} from "@/components/modules/module-config-form";
import { AiSettingsEditor } from "@/components/ai/ai-settings-editor";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/cn";
import type { ControlItem } from "@/lib/api-types";
import {
  useAdminAiConfig,
  useFetchAdminProviderModels,
  usePricingCatalog,
  useUpdateAdminAiConfig,
} from "@/lib/ai-queries";
import { useControlItems, useSetControl } from "@/lib/control-queries";
import { useModuleModels } from "@/lib/queries";
import { toastError } from "@/lib/toast";

export default function AdminControlsPage() {
  const settings = useControlItems("setting");
  const modules = useControlItems("module");

  const forbidden =
    (settings.error instanceof ApiError && settings.error.status === 403) ||
    (modules.error instanceof ApiError && modules.error.status === 403);

  if (forbidden) {
    return (
      <div className="flex items-start gap-2 rounded-md border border-border bg-muted/40 p-3 text-xs text-muted-foreground">
        <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
        <span>
          Controls require the <code>admin</code> role. Ask an administrator to
          grant it in Keycloak (Realm roles → admin).
        </span>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <section className="space-y-3">
        <div>
          <h2 className="text-sm font-semibold">Server settings</h2>
          <p className="text-xs text-muted-foreground">
            Lock a setting to apply one shared, admin-set value to every user.
            Unlocked, each user keeps their own value.
          </p>
        </div>
        {settings.isLoading ? (
          <Skeleton className="h-40 w-full" />
        ) : settings.isError ? (
          <p className="text-xs text-destructive">Failed to load settings.</p>
        ) : (
          (settings.data?.items ?? []).map((item) => (
            <SettingRow key={item.key} item={item} />
          ))
        )}
      </section>

      <section className="space-y-3">
        <div>
          <h2 className="text-sm font-semibold">Modules</h2>
          <p className="text-xs text-muted-foreground">
            Lock a module to set one shared configuration used by every user who
            has it installed.
          </p>
        </div>
        {modules.isLoading ? (
          <Skeleton className="h-40 w-full" />
        ) : modules.isError ? (
          <p className="text-xs text-destructive">Failed to load modules.</p>
        ) : (modules.data?.items ?? []).length === 0 ? (
          <p className="text-xs text-muted-foreground">No modules installed.</p>
        ) : (
          (modules.data?.items ?? []).map((item) => (
            <ModuleRow key={item.key} item={item} />
          ))
        )}
      </section>
    </div>
  );
}

// ── Generic row shell ───────────────────────────────────────────────────────

function ControlHeader({
  item,
  locked,
  onToggle,
  saving,
  collapsible,
  open,
}: {
  item: ControlItem;
  locked: boolean;
  onToggle: (next: boolean) => void;
  saving: boolean;
  collapsible: boolean;
  open: boolean;
}) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div className="flex min-w-0 items-start gap-2">
        {collapsible && (
          <ChevronDown
            className={cn(
              "mt-0.5 h-4 w-4 shrink-0 text-muted-foreground transition-transform",
              open ? "" : "-rotate-90",
            )}
          />
        )}
        <div className="min-w-0">
          <CardTitle className="flex items-center gap-2 text-sm">
            {locked ? (
              <Lock className="h-3.5 w-3.5 text-primary" />
            ) : (
              <Unlock className="h-3.5 w-3.5 text-muted-foreground" />
            )}
            {item.label}
          </CardTitle>
          {item.description && (
            <p className="mt-1 text-xs text-muted-foreground">{item.description}</p>
          )}
        </div>
      </div>
      {/* The lock control is interactive; keep its clicks from toggling the card. */}
      <div
        className="flex shrink-0 items-center gap-2"
        onClick={(e) => e.stopPropagation()}
      >
        <span className="text-xs text-muted-foreground">
          {locked ? "Admin-controlled" : "Per-user"}
        </span>
        <Switch checked={locked} onCheckedChange={onToggle} disabled={saving} />
      </div>
    </div>
  );
}

/** A control card whose whole surface (header + padding) toggles open/closed
 *  once locked. The body is only present when expanded, and stops click
 *  propagation so editing inside it never collapses the card; the lock Switch
 *  does the same in the header. Defaults to collapsed. */
function CollapsibleControlCard({
  item,
  locked,
  onToggleLock,
  saving,
  contentClassName,
  children,
}: {
  item: ControlItem;
  locked: boolean;
  onToggleLock: (next: boolean) => void;
  saving: boolean;
  contentClassName?: string;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const collapsible = locked;
  const toggle = () => setOpen((o) => !o);
  // Keyboard toggle only when the card itself is focused, so Enter/Space inside
  // the body's inputs/switch don't bubble up and collapse it.
  const handleKey = (e: React.KeyboardEvent) => {
    if (e.target === e.currentTarget && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      toggle();
    }
  };
  return (
    <Card
      className={cn(collapsible && "cursor-pointer select-none")}
      role={collapsible ? "button" : undefined}
      tabIndex={collapsible ? 0 : undefined}
      aria-expanded={collapsible ? open : undefined}
      onClick={collapsible ? toggle : undefined}
      onKeyDown={collapsible ? handleKey : undefined}
    >
      <CardHeader>
        <ControlHeader
          item={item}
          locked={locked}
          onToggle={onToggleLock}
          saving={saving}
          collapsible={collapsible}
          open={open}
        />
      </CardHeader>
      {locked && open && (
        <CardContent
          className={cn("cursor-auto select-text", contentClassName)}
          onClick={(e) => e.stopPropagation()}
        >
          {children}
        </CardContent>
      )}
    </Card>
  );
}

// ── Settings rows ───────────────────────────────────────────────────────────

function SettingRow({ item }: { item: ControlItem }) {
  const set = useSetControl("setting");
  const locked = item.control_mode === "admin";

  const onToggle = async (next: boolean) => {
    try {
      await set.mutateAsync({
        key: item.key,
        control_mode: next ? "admin" : "user",
        // Locking keeps any previously stored admin value (server merges); the
        // per-key editor below is how the admin actually sets it.
        admin_value: next ? (item.admin_value ?? undefined) : undefined,
      });
    } catch (e) {
      toastError(`Failed: ${(e as Error).message}`);
    }
  };

  return (
    <CollapsibleControlCard
      item={item}
      locked={locked}
      onToggleLock={onToggle}
      saving={set.isPending}
    >
      {item.key === "ai.config" ? (
        <AiAdminEditor />
      ) : item.key === "worker_concurrency" ? (
        <WorkerConcurrencyEditor item={item} />
      ) : item.key === "analytics.config" ? (
        <AnalyticsEditor item={item} />
      ) : item.key === "cv4cdd.model" ? (
        <Cv4cddModelEditor item={item} />
      ) : (
        <p className="text-xs text-muted-foreground">No editor for this setting.</p>
      )}
    </CollapsibleControlCard>
  );
}

function AiAdminEditor() {
  const { data: stored, isLoading, isError } = useAdminAiConfig();
  const update = useUpdateAdminAiConfig();
  const fetchModels = useFetchAdminProviderModels();
  const { data: pricing } = usePricingCatalog();

  if (isLoading) return <Skeleton className="h-64 w-full" />;
  if (isError || !stored) {
    return <p className="text-xs text-destructive">Failed to load shared AI settings.</p>;
  }

  // Same rich card editor as Settings → AI, but bound to the shared admin value:
  // provider picker, key + Fetch models, model + classifier dropdowns. Saving
  // locks ai.config to admin control (handled server-side).
  return (
    <AiSettingsEditor
      variant="admin"
      stored={stored}
      pricing={pricing}
      saving={update.isPending}
      onSave={(cfg) => update.mutateAsync(cfg)}
      onFetchModels={(provider) => fetchModels.mutateAsync(provider)}
    />
  );
}

function WorkerConcurrencyEditor({ item }: { item: ControlItem }) {
  const set = useSetControl("setting");
  const initial = typeof item.admin_value === "number" ? item.admin_value : 1;
  const [value, setValue] = useState(initial);
  useEffect(() => setValue(initial), [initial]);

  const onSave = async () => {
    try {
      await set.mutateAsync({
        key: "worker_concurrency",
        control_mode: "admin",
        admin_value: value,
      });
      toast.success("Worker concurrency applied");
    } catch (e) {
      toastError(`Save failed: ${(e as Error).message}`);
    }
  };

  return (
    <div className="flex items-end gap-3">
      <div className="space-y-1.5">
        <Label htmlFor="wc">Workers</Label>
        <Input
          id="wc"
          type="number"
          min={1}
          max={8}
          value={value}
          onChange={(e) => setValue(Number(e.target.value))}
          className="w-24"
        />
      </div>
      <Button size="sm" onClick={onSave} disabled={set.isPending} className="cursor-pointer gap-2">
        {set.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
        Apply
      </Button>
    </div>
  );
}

function AnalyticsEditor({ item }: { item: ControlItem }) {
  const set = useSetControl("setting");
  const current =
    item.admin_value && typeof item.admin_value === "object"
      ? ((item.admin_value as { mode?: string }).mode ?? "on")
      : "on";
  const [mode, setMode] = useState(current);
  useEffect(() => setMode(current), [current]);

  const onSave = async () => {
    try {
      await set.mutateAsync({
        key: "analytics.config",
        control_mode: "admin",
        admin_value: { mode, enabled: mode !== "off" },
      });
      toast.success("Analytics policy saved");
    } catch (e) {
      toastError(`Save failed: ${(e as Error).message}`);
    }
  };

  return (
    <div className="flex items-end gap-3">
      <div className="space-y-1.5">
        <Label htmlFor="analytics-mode">Mode</Label>
        <Select value={mode} onValueChange={setMode}>
          <SelectTrigger id="analytics-mode" className="w-40">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="force">Force on (no opt-out)</SelectItem>
            <SelectItem value="on">On (opt-out allowed)</SelectItem>
            <SelectItem value="off">Off</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <Button size="sm" onClick={onSave} disabled={set.isPending} className="cursor-pointer gap-2">
        {set.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
        Apply
      </Button>
    </div>
  );
}

function Cv4cddModelEditor({ item }: { item: ControlItem }) {
  const set = useSetControl("setting");
  // /models is the module's own route (gated on cv4cdd being installed). Lists
  // the platform-shared models so the admin can pick which one to pin.
  const modelsQ = useModuleModels("cv4cdd");
  const initial = typeof item.admin_value === "string" ? item.admin_value : "";
  const [value, setValue] = useState(initial);
  useEffect(() => setValue(initial), [initial]);

  const onSave = async () => {
    try {
      await set.mutateAsync({ key: "cv4cdd.model", control_mode: "admin", admin_value: value });
      toast.success("Shared detection model pinned");
    } catch (e) {
      toastError(`Save failed: ${(e as Error).message}`);
    }
  };

  if (modelsQ.isLoading) return <Skeleton className="h-20 w-full" />;
  if (modelsQ.isError) {
    const notInstalled = modelsQ.error instanceof ApiError && modelsQ.error.status === 404;
    return (
      <p className="text-xs text-muted-foreground">
        {notInstalled
          ? "Install the CV4CDD module to manage its shared model."
          : "Failed to load CV4CDD models."}
      </p>
    );
  }

  const models = modelsQ.data?.models ?? [];
  if (models.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        No CV4CDD models installed yet. Upload one on the module&apos;s settings page first.
      </p>
    );
  }

  return (
    <div className="flex flex-wrap items-end gap-3">
      <div className="w-72 max-w-full space-y-1.5">
        <Label htmlFor="cv4cdd-model">Model</Label>
        <Select value={value || undefined} onValueChange={setValue}>
          <SelectTrigger id="cv4cdd-model" className="w-full font-mono text-xs">
            <SelectValue placeholder="Select a model" />
          </SelectTrigger>
          <SelectContent>
            {models.map((m) => (
              <SelectItem key={m.name} value={m.name} className="font-mono text-xs">
                {m.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <Button
        size="sm"
        onClick={onSave}
        disabled={set.isPending || !value}
        className="cursor-pointer gap-2"
      >
        {set.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
        Apply
      </Button>
    </div>
  );
}

// ── Module rows ─────────────────────────────────────────────────────────────

function ModuleRow({ item }: { item: ControlItem }) {
  const set = useSetControl("module");
  const locked = item.control_mode === "admin";
  const schema = (item.config_schema as ConfigSchema | null) ?? null;
  const properties = useMemo(() => schema?.properties ?? {}, [schema]);
  const hasSchema = Object.keys(properties).length > 0;

  const initial = useMemo(
    () =>
      item.admin_value && typeof item.admin_value === "object"
        ? (item.admin_value as Record<string, unknown>)
        : {},
    [item.admin_value],
  );
  const [draft, setDraft] = useState<Record<string, unknown>>(initial);
  useEffect(() => setDraft(initial), [initial]);

  const onToggle = async (next: boolean) => {
    try {
      await set.mutateAsync({
        key: item.key,
        control_mode: next ? "admin" : "user",
        admin_value: next ? draft : undefined,
      });
    } catch (e) {
      toastError(`Failed: ${(e as Error).message}`);
    }
  };

  const onSave = async () => {
    try {
      await set.mutateAsync({ key: item.key, control_mode: "admin", admin_value: draft });
      toast.success(`${item.label} configuration saved`);
    } catch (e) {
      toastError(`Save failed: ${(e as Error).message}`);
    }
  };

  return (
    <CollapsibleControlCard
      item={item}
      locked={locked}
      onToggleLock={onToggle}
      saving={set.isPending}
      contentClassName="space-y-4"
    >
      {hasSchema ? (
        <ModuleConfigForm
          properties={properties}
          values={draft}
          onChange={(key, val) => setDraft((d) => ({ ...d, [key]: val }))}
        />
      ) : (
        <p className="text-xs text-muted-foreground">
          This module has no configurable parameters; locking simply pins its
          empty config for all users.
        </p>
      )}
      {hasSchema && (
        <div className="flex justify-end">
          <Button
            size="sm"
            onClick={onSave}
            disabled={set.isPending}
            className="cursor-pointer gap-2"
          >
            {set.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            Save shared configuration
          </Button>
        </div>
      )}
    </CollapsibleControlCard>
  );
}
