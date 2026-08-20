"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  Bot,
  Check,
  CircleAlert,
  Download,
  Eye,
  EyeOff,
  Loader2,
  RefreshCw,
  Settings2,
  ShieldAlert,
  Sparkles,
  Upload,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { toast } from "sonner";

import { toastError } from "@/lib/toast";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/cn";
import {
  DEFAULT_AI_CONFIG,
  cleanDisplayName,
  deriveParameters,
  fallbackContext,
  lookupPricing,
  maskedToDraft,
  perMillion,
  providerKeySet,
  type AiConfig,
  type AiConfigOut,
  type AiProvider,
  type FetchModelsResponse,
  type ModelInfo,
  type PricingCatalog,
  type ProviderConfig,
} from "@/lib/ai-queries";

// The editor is shared between the per-user AI settings page (variant="user")
// and the admin shared-config card in Admin → Controls (variant="admin"). The
// parent owns the data source (per-user /ai/* vs admin /admin/controls/ai/*)
// and passes the masked config, save + model-fetch callbacks, and pricing in;
// this component owns all UI state (draft, fetched models, dirty tracking).

export type AiEditorVariant = "user" | "admin";

export interface AiSettingsEditorProps {
  stored: AiConfigOut;
  pricing: PricingCatalog | undefined;
  saving: boolean;
  onSave: (cfg: AiConfig) => Promise<AiConfigOut>;
  onFetchModels: (provider: AiProvider) => Promise<FetchModelsResponse>;
  /** Per-user lock state: disables editing + shows the "controlled" banner.
   *  Only meaningful for variant="user"; admin always edits. */
  controlled?: boolean;
  variant?: AiEditorVariant;
}

// ── Provider catalogue ─────────────────────────────────────────────────────

interface ProviderMeta {
  id: AiProvider;
  label: string;
  tagline: string;
  blurb: string;
  needsBaseUrl: boolean;
  baseUrlPlaceholder?: string;
  keyPlaceholder: string;
  icon: LucideIcon;
}

const PROVIDERS: ProviderMeta[] = [
  {
    id: "anthropic",
    label: "Anthropic",
    tagline: "Claude models",
    blurb: "Key is sent only to api.anthropic.com via the backend proxy.",
    needsBaseUrl: false,
    keyPlaceholder: "sk-ant-…",
    icon: Sparkles,
  },
  {
    id: "openai",
    label: "OpenAI",
    tagline: "GPT and o-series",
    blurb: "Key is proxied through the backend to api.openai.com.",
    needsBaseUrl: false,
    keyPlaceholder: "sk-…",
    icon: Bot,
  },
  {
    id: "unigpt",
    label: "UniGPT",
    tagline: "LibreChat / university deployments",
    blurb:
      "OpenAI-compatible endpoint exposed by a LibreChat or UniGPT deployment. Base URL must include the versioned API prefix (e.g. https://gpt.uni-muenster.de/v1).",
    needsBaseUrl: true,
    baseUrlPlaceholder: "https://gpt.uni-muenster.de/v1",
    keyPlaceholder: "sk-…",
    icon: Settings2,
  },
  {
    id: "custom",
    label: "Custom",
    tagline: "Any OpenAI-compatible endpoint",
    blurb:
      "Self-hosted proxies (vLLM, LM Studio, Ollama with --openai), Azure OpenAI, OpenRouter, etc. Provide the base URL - usually ends with /v1.",
    needsBaseUrl: true,
    baseUrlPlaceholder: "https://your-endpoint.example.com/v1",
    keyPlaceholder: "sk-…",
    icon: Settings2,
  },
];

function providerMeta(id: AiProvider): ProviderMeta {
  return PROVIDERS.find((p) => p.id === id) ?? PROVIDERS[0];
}

function configsEqual(a: AiConfig, b: AiConfig): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** Configured per the masked GET: a stored key (key_set) plus a base URL when
 *  the provider needs one. Used for the saved/"connected" state. */
function maskedProviderConfigured(
  provider: AiProvider | null,
  masked: AiConfigOut,
): boolean {
  if (!provider) return false;
  if (!providerKeySet(masked, provider)) return false;
  const meta = providerMeta(provider);
  if (meta.needsBaseUrl && !masked[`${provider}_base_url`]) return false;
  return true;
}

type ModelsMap = Record<AiProvider, ModelInfo[]>;

const EMPTY_MODELS: ModelsMap = {
  anthropic: [],
  openai: [],
  unigpt: [],
  custom: [],
};

const DEFAULT_MASKED: AiConfigOut = {
  system_prompt: "",
  anthropic_base_url: null,
  openai_base_url: null,
  unigpt_base_url: null,
  custom_base_url: null,
  anthropic_key_set: false,
  openai_key_set: false,
  unigpt_key_set: false,
  custom_key_set: false,
  selected_provider: null,
  selected_model: null,
  classifier_model: null,
  allow_process_data: false,
  controlled_by_admin: false,
};

// ── Editor ──────────────────────────────────────────────────────────────────

export function AiSettingsEditor({
  stored,
  pricing,
  saving,
  onSave,
  onFetchModels,
  controlled = false,
  variant = "user",
}: AiSettingsEditorProps) {
  const [draft, setDraft] = useState<AiConfig>(DEFAULT_AI_CONFIG);
  const [models, setModels] = useState<ModelsMap>(EMPTY_MODELS);
  const [busyProvider, setBusyProvider] = useState<AiProvider | null>(null);
  // Provider ids we've already auto-fetched in this session, so an empty
  // models[] (no models published by the proxy) doesn't trigger a refetch
  // loop and so switching providers + back doesn't double-fetch.
  const [autoFetched, setAutoFetched] = useState<Set<AiProvider>>(new Set());

  const isAdmin = variant === "admin";

  useEffect(() => {
    setDraft(maskedToDraft(stored));
  }, [stored]);

  const dirty = useMemo(
    () => !configsEqual(maskedToDraft(stored), draft),
    [stored, draft],
  );

  const selected = draft.selected_provider;
  const meta = selected ? providerMeta(selected) : null;
  const providerConfig: ProviderConfig | null = selected ? draft[selected] : null;
  const connected =
    maskedProviderConfigured(stored.selected_provider, stored) &&
    Boolean(stored.selected_model);

  // Auto-fetch models for the saved provider on load so the saved model id
  // resolves to a real entry in the dropdown without the user having to
  // click "Fetch models" again every time they revisit.
  useEffect(() => {
    const p = stored.selected_provider;
    if (!p) return;
    if (!maskedProviderConfigured(p, stored)) return;
    if (autoFetched.has(p)) return;
    if (models[p].length > 0) return;
    setAutoFetched((s) => new Set(s).add(p));
    onFetchModels(p)
      .then((res) => setModels((m) => ({ ...m, [p]: res.models })))
      .catch(() => {
        // Silent – surfaced when the user explicitly hits Fetch models.
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stored]);

  const setSelectedProvider = (next: AiProvider | null) => {
    setDraft((d) => ({
      ...d,
      selected_provider: next,
      // Reset model when switching providers – old model id won't apply.
      selected_model: d.selected_provider === next ? d.selected_model : null,
    }));
  };

  const setProviderField = (id: AiProvider, patch: Partial<ProviderConfig>) => {
    setDraft((d) => ({ ...d, [id]: { ...d[id], ...patch } }));
  };

  const saveDraft = async (next: AiConfig = draft): Promise<AiConfigOut> => {
    return onSave(next);
  };

  const onSaveClick = async () => {
    if (controlled) return;
    try {
      await saveDraft();
      toast.success(isAdmin ? "Shared AI settings saved" : "AI settings saved");
    } catch (e) {
      toastError(
        e instanceof ApiError && e.status === 403
          ? "AI settings are controlled by your administrator."
          : `Save failed: ${(e as Error).message}`,
      );
    }
  };

  const onFetch = async (provider: AiProvider) => {
    setBusyProvider(provider);
    try {
      // Persist any pending edits first so the backend reads the same key.
      if (dirty) await saveDraft();
      const res = await onFetchModels(provider);
      setModels((m) => ({ ...m, [provider]: res.models }));
      toast.success(`Fetched ${res.models.length} model${res.models.length === 1 ? "" : "s"}`);
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? typeof e.detail === "object" && e.detail !== null
            ? JSON.stringify(e.detail)
            : String(e.detail)
          : (e as Error).message;
      toastError(`Could not fetch models: ${msg}`);
    } finally {
      setBusyProvider(null);
    }
  };

  return (
    <div className="space-y-4">
      {variant === "user" && controlled && (
        <div className="flex items-start gap-2.5 rounded-lg border border-border bg-muted/40 px-3 py-2 text-sm text-muted-foreground">
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
          <div className="min-w-0">
            <div className="font-medium text-foreground">Controlled by your administrator</div>
            <p className="text-xs">
              AI settings are managed centrally and shared across all users. They
              are read-only here; AI features use the administrator&apos;s key.
            </p>
          </div>
        </div>
      )}

      {isAdmin && (
        <div className="flex items-start gap-2.5 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-400">
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            This configuration is shared with every user while AI is locked. Keys
            are never displayed back – leave a key field blank to keep the stored one.
          </span>
        </div>
      )}

      <ConnectionBanner
        connected={connected}
        draftProvider={draft.selected_provider}
        variant={variant}
      />

      <ProviderPicker
        selected={draft.selected_provider}
        configuredProviders={
          new Set(
            (["anthropic", "openai", "unigpt", "custom"] as AiProvider[]).filter((k) =>
              maskedProviderConfigured(k, stored),
            ),
          )
        }
        onSelect={controlled ? () => {} : setSelectedProvider}
        disabled={controlled}
      />

      {selected && meta && providerConfig && (
        <>
          <ProviderConfigCard
            meta={meta}
            config={providerConfig}
            storedKeySet={providerKeySet(stored, selected)}
            onChange={(patch) => setProviderField(selected, patch)}
            onFetch={() => onFetch(selected)}
            busy={busyProvider === selected}
            models={models[selected]}
            pricing={pricing}
            disabled={controlled}
          />

          <ModelSelectCard
            provider={selected}
            selectedModel={draft.selected_model}
            models={models[selected]}
            onChange={(m) => setDraft((d) => ({ ...d, selected_model: m }))}
            disabled={controlled}
          />

          <ClassifierModelCard
            provider={selected}
            classifierModel={draft.classifier_model}
            defaultModel={draft.selected_model}
            models={models[selected]}
            onChange={(m) => setDraft((d) => ({ ...d, classifier_model: m }))}
            disabled={controlled}
          />
        </>
      )}

      <ProcessDataCard
        enabled={draft.allow_process_data}
        onChange={(v) => setDraft((d) => ({ ...d, allow_process_data: v }))}
        disabled={controlled}
        variant={variant}
      />

      <SystemPromptCard
        value={draft.system_prompt}
        onChange={(v) => setDraft((d) => ({ ...d, system_prompt: v }))}
        disabled={controlled}
      />

      {!controlled && (
        <SaveBar
          dirty={dirty}
          saving={saving}
          onSave={onSaveClick}
          onRevert={() => setDraft(maskedToDraft(stored))}
          label={isAdmin ? "Save shared settings" : "Save"}
        />
      )}
    </div>
  );
}

// ── Connection banner ──────────────────────────────────────────────────────

function ConnectionBanner({
  connected,
  draftProvider,
  variant,
}: {
  connected: boolean;
  draftProvider: AiProvider | null;
  variant: AiEditorVariant;
}) {
  if (connected) {
    return (
      <div className="flex items-center gap-2.5 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-700 dark:text-emerald-400">
        <Check className="h-4 w-4 shrink-0" />
        <span>
          {variant === "admin"
            ? "Shared AI is connected. Every user uses this key and model."
            : "AI is connected. All AI features are available across the platform."}
        </span>
      </div>
    );
  }
  return (
    <div className="flex items-start gap-2.5 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-400">
      <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="min-w-0">
        <div className="font-medium">No AI connected</div>
        <p className="text-xs text-amber-700/80 dark:text-amber-300/80">
          {draftProvider
            ? `Add an API key for ${providerMeta(draftProvider).label}, pick a model, then click Save.`
            : "Pick a provider below, paste an API key, choose a model, then click Save."}
        </p>
      </div>
    </div>
  );
}

// ── Provider picker (4 cards) ──────────────────────────────────────────────

function ProviderPicker({
  selected,
  configuredProviders,
  onSelect,
  disabled = false,
}: {
  selected: AiProvider | null;
  configuredProviders: Set<AiProvider>;
  onSelect: (next: AiProvider | null) => void;
  disabled?: boolean;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Provider</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {PROVIDERS.map((p) => {
            const Icon = p.icon;
            const active = selected === p.id;
            const isConfigured = configuredProviders.has(p.id);
            return (
              <button
                key={p.id}
                type="button"
                onClick={() => onSelect(active ? null : p.id)}
                disabled={disabled}
                className={cn(
                  "group relative flex flex-col items-start gap-1.5 rounded-md border bg-card p-3 text-left transition-colors",
                  disabled ? "cursor-not-allowed opacity-70" : "cursor-pointer",
                  active
                    ? "border-primary ring-1 ring-primary"
                    : "border-border hover:border-primary/50",
                )}
                aria-pressed={active}
              >
                <div className="flex w-full items-center justify-between">
                  <div
                    className={cn(
                      "flex h-6 w-6 items-center justify-center rounded-md",
                      active ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground",
                    )}
                  >
                    <Icon className="h-3.5 w-3.5" />
                  </div>
                  {isConfigured && (
                    <Check
                      className={cn(
                        "h-3.5 w-3.5",
                        active ? "text-primary" : "text-emerald-500",
                      )}
                    />
                  )}
                </div>
                <div className="text-sm font-medium">{p.label}</div>
                <div className="text-[11px] leading-tight text-muted-foreground">
                  {p.tagline}
                </div>
              </button>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}

// ── Provider config card (single, for selected provider) ───────────────────

function ProviderConfigCard({
  meta,
  config,
  storedKeySet,
  onChange,
  onFetch,
  busy,
  models,
  pricing,
  disabled = false,
}: {
  meta: ProviderMeta;
  config: ProviderConfig;
  storedKeySet: boolean;
  onChange: (patch: Partial<ProviderConfig>) => void;
  onFetch: () => void;
  busy: boolean;
  models: ModelInfo[];
  pricing: PricingCatalog | undefined;
  disabled?: boolean;
}) {
  const [showKey, setShowKey] = useState(false);
  // A stored key (masked, blank in the draft) still permits Fetch – the backend
  // uses the persisted key.
  const canFetch =
    !disabled && Boolean((config.api_key || storedKeySet) && (!meta.needsBaseUrl || config.base_url));

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{meta.label} configuration</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-xs text-muted-foreground">{meta.blurb}</p>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor={`${meta.id}-key`}>
              API key <span className="text-destructive">*</span>
            </Label>
            <div className="relative">
              <Input
                id={`${meta.id}-key`}
                type={showKey ? "text" : "password"}
                autoComplete="off"
                spellCheck={false}
                value={config.api_key ?? ""}
                onChange={(e) => onChange({ api_key: e.target.value || null })}
                placeholder={storedKeySet ? "•••••••• (stored – leave blank to keep)" : meta.keyPlaceholder}
                disabled={disabled}
                className="pr-9 font-mono text-xs"
              />
              <button
                type="button"
                aria-label={showKey ? "Hide key" : "Show key"}
                onClick={() => setShowKey((s) => !s)}
                className="absolute inset-y-0 right-0 flex w-9 cursor-pointer items-center justify-center text-muted-foreground hover:text-foreground"
              >
                {showKey ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
              </button>
            </div>
          </div>

          {meta.needsBaseUrl && (
            <div className="space-y-1.5">
              <Label htmlFor={`${meta.id}-base`}>
                Base URL <span className="text-destructive">*</span>
              </Label>
              <Input
                id={`${meta.id}-base`}
                type="url"
                autoComplete="off"
                spellCheck={false}
                value={config.base_url ?? ""}
                onChange={(e) => onChange({ base_url: e.target.value || null })}
                placeholder={meta.baseUrlPlaceholder}
                disabled={disabled}
                className="font-mono text-xs"
              />
            </div>
          )}
        </div>

        <div className="flex items-center justify-between gap-2">
          <span className="text-xs text-muted-foreground tabular-nums">
            {models.length > 0
              ? `${models.length} model${models.length === 1 ? "" : "s"} available`
              : "No models fetched yet - paste a key and click Fetch models"}
          </span>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="cursor-pointer gap-1.5"
            onClick={onFetch}
            disabled={!canFetch || busy}
          >
            {busy ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" />
            )}
            Fetch models
          </Button>
        </div>

        {models.length > 0 && <ModelsTable provider={meta.id} models={models} pricing={pricing} />}
      </CardContent>
    </Card>
  );
}

// ── Default-model selector ─────────────────────────────────────────────────

function ModelSelectCard({
  provider,
  selectedModel,
  models,
  onChange,
  disabled = false,
}: {
  provider: AiProvider;
  selectedModel: string | null;
  models: ModelInfo[];
  onChange: (modelId: string | null) => void;
  disabled?: boolean;
}) {
  // If the saved model isn't in the fetched list yet (just loaded, or the
  // provider's catalog has changed), keep the saved id visible as its own item
  // so the user can see what's selected without re-fetching.
  const savedNotInList =
    selectedModel !== null && !models.some((m) => m.id === selectedModel);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Default model</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-muted-foreground">
          Used by MATE AI and the panel-level AI insights when a model isn&apos;t
          specified per-call.
        </p>
        <Select
          value={selectedModel ?? "__none"}
          onValueChange={(v) => onChange(v === "__none" ? null : v)}
          disabled={disabled}
        >
          <SelectTrigger className="text-xs">
            <SelectValue placeholder="Choose a model" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__none">None</SelectItem>
            {savedNotInList && selectedModel && (
              <SelectItem value={selectedModel}>
                {cleanDisplayName(selectedModel)}{" "}
                <span className="text-muted-foreground">(saved)</span>
              </SelectItem>
            )}
            {models.map((m) => (
              <SelectItem key={m.id} value={m.id}>
                {m.display_name ?? cleanDisplayName(m.id)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {models.length === 0 && (
          <p className="text-xs text-muted-foreground">
            No models fetched yet for {providerMeta(provider).label}. Use{" "}
            <em>Fetch models</em> in the configuration card above to see
            other options.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

// ── Classifier model (optional, cheaper) ───────────────────────────────────

function ClassifierModelCard({
  provider,
  classifierModel,
  defaultModel,
  models,
  onChange,
  disabled = false,
}: {
  provider: AiProvider;
  classifierModel: string | null;
  defaultModel: string | null;
  models: ModelInfo[];
  onChange: (modelId: string | null) => void;
  disabled?: boolean;
}) {
  const savedNotInList =
    classifierModel !== null && !models.some((m) => m.id === classifierModel);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Navigation classifier model</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-muted-foreground">
          MATE AI runs a tiny intent check on each message to suggest in-app
          navigation. Pick a cheaper/faster model here to keep that overhead low.
          Leave on <em>Same as default</em> to reuse the default model.
        </p>
        <Select
          value={classifierModel ?? "__default"}
          onValueChange={(v) => onChange(v === "__default" ? null : v)}
          disabled={disabled}
        >
          <SelectTrigger className="text-xs">
            <SelectValue placeholder="Same as default model" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__default">
              Same as default
              {defaultModel ? ` (${cleanDisplayName(defaultModel)})` : ""}
            </SelectItem>
            {savedNotInList && classifierModel && (
              <SelectItem value={classifierModel}>
                {cleanDisplayName(classifierModel)}{" "}
                <span className="text-muted-foreground">(saved)</span>
              </SelectItem>
            )}
            {models.map((m) => (
              <SelectItem key={m.id} value={m.id}>
                {m.display_name ?? cleanDisplayName(m.id)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {models.length === 0 && (
          <p className="text-xs text-muted-foreground">
            No models fetched yet for {providerMeta(provider).label}.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

// ── Process-data access (opt-in, sensitive) ─────────────────────────────────

function ProcessDataCard({
  enabled,
  onChange,
  disabled = false,
  variant,
}: {
  enabled: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
  variant: AiEditorVariant;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Access process data</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-start justify-between gap-4">
          <div className="space-y-1">
            <Label htmlFor="allow-process-data" className="cursor-pointer">
              {variant === "admin"
                ? "Let MATE AI read users' process data"
                : "Let MATE AI read your process data"}
            </Label>
            <p className="text-xs text-muted-foreground">
              Enables data answers like &quot;how many variants does Process X have?&quot;.
              Navigating to a process&apos;s modules by name works regardless of this setting.
            </p>
          </div>
          <Switch
            id="allow-process-data"
            checked={enabled}
            onCheckedChange={onChange}
            disabled={disabled}
          />
        </div>
        {enabled && (
          <div className="flex items-start gap-2.5 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-400">
            <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" />
            <span>
              When enabled, process statistics (e.g. case, event and variant counts)
              are sent to the configured AI provider to answer data questions. Only turn
              this on if sharing this potentially sensitive data with that provider is
              acceptable.
            </span>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ── System prompt ──────────────────────────────────────────────────────────

function SystemPromptCard({
  value,
  onChange,
  disabled = false,
}: {
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
}) {
  const fileRef = useRef<HTMLInputElement>(null);

  const onFile = async (file: File) => {
    try {
      const text = await file.text();
      onChange(text);
      toast.success(`Loaded ${file.name} (${text.length.toLocaleString()} chars)`);
    } catch (e) {
      toastError(`Could not read file: ${(e as Error).message}`);
    }
  };

  const onDownload = () => {
    const blob = new Blob([value], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "system-prompt.txt";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-base">System prompt</CardTitle>
          <div className="flex items-center gap-2">
            <input
              ref={fileRef}
              type="file"
              accept=".txt,.md,text/plain,text/markdown"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) onFile(f);
                e.target.value = "";
              }}
            />
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="cursor-pointer gap-1.5"
              onClick={() => fileRef.current?.click()}
              disabled={disabled}
            >
              <Upload className="h-3.5 w-3.5" />
              Load from file
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="cursor-pointer gap-1.5"
              onClick={onDownload}
              disabled={!value}
            >
              <Download className="h-3.5 w-3.5" />
              Download
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-2">
        <p className="text-xs text-muted-foreground">
          Prepended to every MATE AI conversation and every AI insight call.
          Shared across all providers. Plain text or markdown; whitespace is preserved.
        </p>
        <textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          rows={10}
          disabled={disabled}
          placeholder="You are MATE, an analyst assistant…"
          className={cn(
            "block w-full resize-y rounded-md border border-input bg-transparent px-3 py-2 font-mono text-xs leading-relaxed shadow-xs",
            "focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none",
            "placeholder:text-muted-foreground",
          )}
        />
        <p className="text-[10px] text-muted-foreground tabular-nums">
          {value.length.toLocaleString()} characters
        </p>
      </CardContent>
    </Card>
  );
}

// ── Save bar ───────────────────────────────────────────────────────────────

function SaveBar({
  dirty,
  saving,
  onSave,
  onRevert,
  label,
}: {
  dirty: boolean;
  saving: boolean;
  onSave: () => void;
  onRevert: () => void;
  label: string;
}) {
  return (
    <div className="sticky bottom-0 z-10 flex items-center justify-end gap-2 border-t border-border bg-background/80 py-3 backdrop-blur">
      {dirty && (
        <span className="text-xs text-muted-foreground mr-auto">Unsaved changes</span>
      )}
      <Button
        variant="outline"
        size="sm"
        className="cursor-pointer"
        onClick={onRevert}
        disabled={!dirty || saving}
      >
        Revert
      </Button>
      <Button
        size="sm"
        className="cursor-pointer"
        onClick={onSave}
        disabled={!dirty || saving}
      >
        {saving ? "Saving…" : label}
      </Button>
    </div>
  );
}

// ── Models table ───────────────────────────────────────────────────────────

type CostCell = number | "free" | null;

function ModelsTable({
  provider,
  models,
  pricing,
}: {
  provider: AiProvider;
  models: ModelInfo[];
  pricing: PricingCatalog | undefined;
}) {
  const rows = useMemo(
    () =>
      [...models].sort((a, b) => a.id.localeCompare(b.id)).map((m) => {
        const price = lookupPricing(pricing, m.id);
        // UniGPT is a free university-hosted LibreChat – costs are always $0.
        // Custom is unknown; show the catalog price if we have one.
        const isFree = provider === "unigpt";
        const context =
          price?.max_input_tokens ?? price?.max_tokens ?? fallbackContext(m.id) ?? null;
        return {
          id: m.id,
          display: m.display_name ?? cleanDisplayName(m.id),
          params: deriveParameters(m.id),
          input: (isFree ? "free" : perMillion(price?.input_cost_per_token)) as CostCell,
          output: (isFree ? "free" : perMillion(price?.output_cost_per_token)) as CostCell,
          context,
        };
      }),
    [models, pricing, provider],
  );

  return (
    <div className="rounded-md border border-border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="text-xs">Model</TableHead>
            <TableHead className="text-right text-xs">Params</TableHead>
            <TableHead className="text-right text-xs">Input $/M</TableHead>
            <TableHead className="text-right text-xs">Output $/M</TableHead>
            <TableHead className="text-right text-xs">Context</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((r) => (
            <TableRow key={`${provider}-${r.id}`}>
              <TableCell className="font-mono text-xs">{r.display}</TableCell>
              <TableCell className="text-right text-xs tabular-nums text-muted-foreground">
                {r.params ?? "–"}
              </TableCell>
              <TableCell className="text-right text-xs tabular-nums text-muted-foreground">
                {formatCost(r.input)}
              </TableCell>
              <TableCell className="text-right text-xs tabular-nums text-muted-foreground">
                {formatCost(r.output)}
              </TableCell>
              <TableCell className="text-right text-xs tabular-nums text-muted-foreground">
                {r.context !== null ? formatContext(r.context) : "–"}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function formatCost(v: CostCell): React.ReactNode {
  if (v === "free") {
    return <span className="text-emerald-600 dark:text-emerald-400">Free</span>;
  }
  if (v === null) return "–";
  if (v >= 100) return `$${v.toFixed(0)}`;
  if (v >= 1) return `$${v.toFixed(2)}`;
  return `$${v.toFixed(3)}`;
}

function formatContext(tokens: number): string {
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(1)}M`;
  if (tokens >= 1_000) return `${Math.round(tokens / 1_000)}K`;
  return tokens.toLocaleString();
}
