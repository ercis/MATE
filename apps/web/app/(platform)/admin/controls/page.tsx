"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronDown,
  HardDriveUpload,
  Loader2,
  Lock,
  ShieldAlert,
  Trash2,
  Unlock,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
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
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import {
  ModuleConfigForm,
  type ConfigSchema,
} from "@/components/modules/module-config-form";
import { AiSettingsEditor } from "@/components/ai/ai-settings-editor";
import { AiModelPicker, type AiModelSelection } from "@/components/ai/ai-model-picker";
import {
  ModuleOpenAiCard,
  EMPTY_MODULE_AI_DRAFT,
  readModuleAiDraft,
  type ModuleAiDraft,
} from "@/components/ai/module-openai-card";
import { UploadProgress } from "@/components/settings/upload-progress";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/cn";
import type { ControlItem } from "@/lib/api-types";
import {
  useAdminAiConfig,
  useFetchAdminProviderModels,
  usePricingCatalog,
  useUpdateAdminAiConfig,
  type AiProvider,
} from "@/lib/ai-queries";
import { useControlItems, useSetControl } from "@/lib/control-queries";
import {
  useDeleteModuleModel,
  useModuleModels,
  useUploadModuleModel,
  type AiModelsManifest,
} from "@/lib/queries";
import { toastError } from "@/lib/toast";

function formatBytes(bytes: number): string {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  return `${(bytes / 1024 ** i).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

/** Group per-card control items by their module, preserving card order. */
function groupCardsByModule(
  items: ControlItem[],
): { moduleId: string; label: string; items: ControlItem[] }[] {
  const map = new Map<string, { label: string; items: ControlItem[] }>();
  for (const it of items) {
    const mid = it.module_id ?? it.key.split(":")[0];
    const g = map.get(mid) ?? { label: it.label || mid, items: [] };
    g.items.push(it);
    map.set(mid, g);
  }
  return [...map.entries()].map(([moduleId, g]) => ({ moduleId, ...g }));
}

export default function AdminControlsPage() {
  const settings = useControlItems("setting");
  const cards = useControlItems("card");

  const forbidden =
    (settings.error instanceof ApiError && settings.error.status === 403) ||
    (cards.error instanceof ApiError && cards.error.status === 403);

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

  const serverSettings = settings.data?.items ?? [];
  const groups = groupCardsByModule(cards.data?.items ?? []);

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
          serverSettings.map((item) => <SettingRow key={item.key} item={item} />)
        )}
      </section>

      <section className="space-y-3">
        <div>
          <h2 className="text-sm font-semibold">Modules</h2>
          <p className="text-xs text-muted-foreground">
            Lock an individual settings card (Configuration, AI models, detection
            model) to pin it for every user who has the module installed. Each
            card is independent; the others stay per-user.
          </p>
        </div>
        {cards.isLoading ? (
          <Skeleton className="h-40 w-full" />
        ) : cards.isError ? (
          <p className="text-xs text-destructive">Failed to load modules.</p>
        ) : groups.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            No installed module exposes lockable settings.
          </p>
        ) : (
          groups.map((g) => (
            <ModuleCardGroup key={g.moduleId} moduleId={g.moduleId} label={g.label} items={g.items} />
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

/** Generalized model-store card manager: upload (with progress), pick the
 *  shared model, lock it for every user. Driven by the module's `model_store`
 *  manifest; the lock toggle + pin write the module's model card control
 *  ("<module_id>:model") as `{ [config_key]: name }`, while uploads/deletes hit
 *  the module's own `/models` route (platform-shared storage). */
function ModelCardControl({ item }: { item: ControlItem }) {
  const set = useSetControl("card");
  const moduleId = item.module_id ?? item.key.split(":")[0];
  const store = item.model_store ?? null;
  const configKey = store?.config_key ?? "model";
  const accept = store?.accept ?? ".tar.zst";
  const title = item.title ?? store?.title ?? "Model files";

  const modelsQ = useModuleModels(moduleId);
  const upload = useUploadModuleModel(moduleId);
  const remove = useDeleteModuleModel(moduleId);
  const fileRef = useRef<HTMLInputElement>(null);

  const locked = item.control_mode === "admin";
  const av =
    item.admin_value && typeof item.admin_value === "object"
      ? (item.admin_value as Record<string, unknown>)
      : {};
  const pinned = typeof av[configKey] === "string" ? (av[configKey] as string) : "";
  const [value, setValue] = useState(pinned);
  useEffect(() => setValue(pinned), [pinned]);

  const onToggleLock = async (next: boolean) => {
    try {
      const chosen = value || pinned;
      await set.mutateAsync({
        key: item.key,
        control_mode: next ? "admin" : "user",
        // Locking keeps the current pin (or in-progress selection); the picker
        // + Apply below is how the admin changes it. Locking with nothing chosen
        // stores no value yet (the module then reports read-only, no pin).
        admin_value: next ? (chosen ? { [configKey]: chosen } : undefined) : undefined,
      });
      toast.success(
        next
          ? "Model locked for all users"
          : "Model unlocked – each user picks their own",
      );
    } catch (e) {
      toastError(`Failed: ${(e as Error).message}`);
    }
  };

  const onApply = async () => {
    try {
      await set.mutateAsync({
        key: item.key,
        control_mode: "admin",
        admin_value: { [configKey]: value },
      });
      toast.success("Shared model pinned");
    } catch (e) {
      toastError(`Save failed: ${(e as Error).message}`);
    }
  };

  const onFilePicked = async (file: File | undefined) => {
    if (!file) return;
    try {
      const res = await upload.mutateAsync(file);
      toast.success(`Installed model "${res.name}"`);
      // Nothing pinned yet? Preselect the first upload so Apply is one click.
      if (!value) setValue(res.name);
    } catch (e) {
      toastError(`Upload failed: ${(e as Error).message}`);
    } finally {
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const onDelete = async (name: string) => {
    try {
      await remove.mutateAsync(name);
      toast.success(`Deleted model "${name}"`);
    } catch (e) {
      toastError(`Delete failed: ${(e as Error).message}`);
    }
  };

  const notInstalled =
    modelsQ.isError && modelsQ.error instanceof ApiError && modelsQ.error.status === 404;
  const models = modelsQ.data?.models ?? [];

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <CardTitle className="flex items-center gap-2 text-sm">
              {locked ? (
                <Lock className="h-3.5 w-3.5 text-primary" />
              ) : (
                <Unlock className="h-3.5 w-3.5 text-muted-foreground" />
              )}
              {title}
            </CardTitle>
            <p className="mt-1 text-xs text-muted-foreground">
              Upload and pin one shared model for every user. Unlocked, each user
              picks their own on the module&apos;s settings page.
            </p>
          </div>
          <div
            className="flex shrink-0 items-center gap-2"
            onClick={(e) => e.stopPropagation()}
          >
            <span className="text-xs text-muted-foreground">
              {locked ? "Admin-controlled" : "Per-user"}
            </span>
            <Switch checked={locked} onCheckedChange={onToggleLock} disabled={set.isPending} />
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {notInstalled ? (
          <p className="text-xs text-muted-foreground">
            Install this module to manage its shared model.
          </p>
        ) : (
          <>
            <div className="space-y-2">
              <div className="flex items-center gap-3">
                <input
                  ref={fileRef}
                  type="file"
                  accept={accept}
                  className="hidden"
                  onChange={(e) => onFilePicked(e.target.files?.[0])}
                />
                <Button
                  size="sm"
                  variant="outline"
                  className="cursor-pointer gap-2"
                  disabled={upload.isPending}
                  onClick={() => fileRef.current?.click()}
                >
                  {upload.isPending ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <HardDriveUpload className="h-3.5 w-3.5" />
                  )}
                  {upload.isPending ? "Uploading…" : "Upload model"}
                </Button>
                <span className="text-xs text-muted-foreground">
                  Accepts <code className="rounded bg-muted px-1 py-0.5">{accept}</code> ·
                  shared platform-wide
                </span>
              </div>
              <UploadProgress isPending={upload.isPending} progress={upload.progress} />
            </div>

            {modelsQ.isLoading ? (
              <Skeleton className="h-20 w-full" />
            ) : models.length === 0 ? (
              <p className="rounded-md border border-dashed py-6 text-center text-xs text-muted-foreground">
                No models installed yet. Upload a {accept} archive to get started.
              </p>
            ) : (
              <RadioGroup
                value={value || undefined}
                onValueChange={setValue}
                disabled={!locked}
                className="gap-2"
              >
                {models.map((m) => (
                  <div
                    key={m.name}
                    className="flex items-center justify-between gap-3 rounded-md border px-3 py-2"
                  >
                    <div className="flex items-center gap-3">
                      <RadioGroupItem id={`pin-${moduleId}-${m.name}`} value={m.name} disabled={!locked} />
                      <Label
                        htmlFor={`pin-${moduleId}-${m.name}`}
                        className="cursor-pointer font-mono text-xs"
                      >
                        {m.name}
                      </Label>
                      {m.active && (
                        <Badge variant="secondary" className="h-5 px-1.5 py-0 text-[10px]">
                          in use
                        </Badge>
                      )}
                    </div>
                    <div className="flex items-center gap-3">
                      <span className="tabular-nums text-[11px] text-muted-foreground">
                        {formatBytes(m.size_bytes)}
                      </span>
                      <AlertDialog>
                        <AlertDialogTrigger asChild>
                          <Button
                            size="icon"
                            variant="ghost"
                            className="h-7 w-7 cursor-pointer text-muted-foreground hover:text-destructive"
                            disabled={remove.isPending}
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </Button>
                        </AlertDialogTrigger>
                        <AlertDialogContent>
                          <AlertDialogHeader>
                            <AlertDialogTitle>Delete model “{m.name}”?</AlertDialogTitle>
                            <AlertDialogDescription>
                              This removes the model from disk for{" "}
                              <strong>every account on the platform</strong>. Anyone
                              currently using it will need to pick another model.
                            </AlertDialogDescription>
                          </AlertDialogHeader>
                          <AlertDialogFooter>
                            <AlertDialogCancel className="cursor-pointer">Cancel</AlertDialogCancel>
                            <AlertDialogAction
                              onClick={() => onDelete(m.name)}
                              className="cursor-pointer bg-destructive text-destructive-foreground hover:bg-destructive/90"
                            >
                              Delete
                            </AlertDialogAction>
                          </AlertDialogFooter>
                        </AlertDialogContent>
                      </AlertDialog>
                    </div>
                  </div>
                ))}
              </RadioGroup>
            )}

            {locked && models.length > 0 && (
              <div className="flex items-center justify-between gap-3">
                <p className="text-xs text-muted-foreground">
                  Selected model applies to every user.
                </p>
                <Button
                  size="sm"
                  onClick={onApply}
                  disabled={set.isPending || !value || value === pinned}
                  className="cursor-pointer gap-2"
                >
                  {set.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                  Apply
                </Button>
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ── Module card groups ──────────────────────────────────────────────────────

const CARD_HINTS: Record<string, string> = {
  config: "Pins the module's configuration parameters for every user.",
  ai: "Pins the AI provider/model (and key for self-hosted modules) for every user.",
  model: "Pins one shared uploaded model for every user.",
};

/** Present a card control item to the shared shell with the card's title as the
 *  header label and a short per-card hint as the description. */
function displayItem(item: ControlItem): ControlItem {
  return {
    ...item,
    label: item.title ?? item.card_id ?? item.key,
    description: CARD_HINTS[item.card_id ?? ""] ?? null,
  };
}

/** One module, with its per-card lock rows and a bulk "Unlock all". */
function ModuleCardGroup({
  moduleId,
  label,
  items,
}: {
  moduleId: string;
  label: string;
  items: ControlItem[];
}) {
  const set = useSetControl("card");
  const anyLocked = items.some((it) => it.control_mode === "admin");

  const onUnlockAll = async () => {
    try {
      await Promise.all(
        items
          .filter((it) => it.control_mode === "admin")
          .map((it) => set.mutateAsync({ key: it.key, control_mode: "user" })),
      );
      toast.success(`${label} unlocked for all users`);
    } catch (e) {
      toastError(`Failed: ${(e as Error).message}`);
    }
  };

  return (
    <div className="space-y-2 rounded-lg border border-border/60 p-3">
      <div className="flex items-center justify-between gap-4 px-1">
        <h3 className="text-sm font-medium" title={moduleId}>
          {label}
        </h3>
        {anyLocked && (
          <Button
            size="sm"
            variant="ghost"
            onClick={onUnlockAll}
            disabled={set.isPending}
            className="cursor-pointer gap-2 text-xs"
          >
            {set.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            Unlock all
          </Button>
        )}
      </div>
      <div className="space-y-2">
        {items.map((it) => (
          <CardControlRow key={it.key} item={it} />
        ))}
      </div>
    </div>
  );
}

function CardControlRow({ item }: { item: ControlItem }) {
  if (item.card_id === "model") return <ModelCardControl item={item} />;
  if (item.card_id === "ai") return <AiCardControl item={item} />;
  return <ConfigCardControl item={item} />;
}

/** The Configuration card: lock + edit the module's config_schema props. */
function ConfigCardControl({ item }: { item: ControlItem }) {
  const set = useSetControl("card");
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
      toast.success("Configuration saved");
    } catch (e) {
      toastError(`Save failed: ${(e as Error).message}`);
    }
  };

  return (
    <CollapsibleControlCard
      item={displayItem(item)}
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
          This module has no configurable parameters.
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

function readPlatformAi(
  ai: Record<string, unknown> | undefined,
): { llm: AiModelSelection; embedding: AiModelSelection } {
  const a = ai ?? {};
  const llm = (a.llm as Partial<AiModelSelection> | undefined) ?? {};
  const emb = (a.embedding as Partial<AiModelSelection> | undefined) ?? {};
  return {
    llm: { provider: llm.provider ?? null, model: llm.model ?? null },
    embedding: {
      provider: emb.provider ?? null,
      model: emb.model ?? null,
      dimensions: emb.dimensions ?? null,
    },
  };
}

const EMBEDDING_PROVIDERS: AiProvider[] = ["openai", "unigpt", "custom"];

/** The AI models card: lock + edit the shared AI selection. Mirrors the user
 *  settings page (self-hosted key card vs platform-keyed pickers) and stores
 *  the same `config_json.ai` shape the module reads. */
function AiCardControl({ item }: { item: ControlItem }) {
  const set = useSetControl("card");
  const moduleId = item.module_id ?? item.key.split(":")[0];
  const locked = item.control_mode === "admin";
  const ai = (item.ai_models as AiModelsManifest | null) ?? null;
  const selfHosted = Boolean(ai?.self_hosted);
  const savedAi =
    item.admin_value && typeof item.admin_value === "object"
      ? ((item.admin_value as Record<string, unknown>).ai as Record<string, unknown> | undefined)
      : undefined;

  const [moduleDraft, setModuleDraft] = useState<ModuleAiDraft>(EMPTY_MODULE_AI_DRAFT);
  const [platformDraft, setPlatformDraft] = useState(() => readPlatformAi(savedAi));
  useEffect(() => {
    setModuleDraft(readModuleAiDraft({ ai: savedAi ?? {} }));
    setPlatformDraft(readPlatformAi(savedAi));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [item.admin_value]);

  const aiValue = (): Record<string, unknown> =>
    selfHosted
      ? {
          api_key: moduleDraft.api_key,
          llm_model: moduleDraft.llm_model,
          embedding_model: moduleDraft.embedding_model,
          embedding_dimensions: moduleDraft.embedding_dimensions,
        }
      : { llm: platformDraft.llm, embedding: platformDraft.embedding };

  const onToggle = async (next: boolean) => {
    try {
      await set.mutateAsync({
        key: item.key,
        control_mode: next ? "admin" : "user",
        admin_value: next ? { ai: aiValue() } : undefined,
      });
    } catch (e) {
      toastError(`Failed: ${(e as Error).message}`);
    }
  };

  const onSave = async () => {
    try {
      await set.mutateAsync({ key: item.key, control_mode: "admin", admin_value: { ai: aiValue() } });
      toast.success("AI models saved");
    } catch (e) {
      toastError(`Save failed: ${(e as Error).message}`);
    }
  };

  return (
    <CollapsibleControlCard
      item={displayItem(item)}
      locked={locked}
      onToggleLock={onToggle}
      saving={set.isPending}
      contentClassName="space-y-4"
    >
      {selfHosted ? (
        <ModuleOpenAiCard
          moduleId={moduleId}
          savedApiKey={moduleDraft.api_key}
          llmSlot={ai?.llm ?? null}
          embeddingSlot={ai?.embedding ?? null}
          value={moduleDraft}
          onChange={setModuleDraft}
        />
      ) : (
        <>
          {ai?.llm && (
            <AiModelPicker
              title={ai.llm.title}
              description={ai.llm.description}
              value={platformDraft.llm}
              onChange={(next) => setPlatformDraft((d) => ({ ...d, llm: next }))}
            />
          )}
          {ai?.embedding && (
            <AiModelPicker
              title={ai.embedding.title}
              description={ai.embedding.description}
              value={platformDraft.embedding}
              onChange={(next) => setPlatformDraft((d) => ({ ...d, embedding: next }))}
              allowProviders={EMBEDDING_PROVIDERS}
              preferEmbeddingModels
              showDimensions
            />
          )}
        </>
      )}
      <div className="flex justify-end">
        <Button
          size="sm"
          onClick={onSave}
          disabled={set.isPending}
          className="cursor-pointer gap-2"
        >
          {set.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          Save shared AI models
        </Button>
      </div>
    </CollapsibleControlCard>
  );
}
