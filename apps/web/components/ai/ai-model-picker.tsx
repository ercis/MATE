"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Loader2, RefreshCw } from "lucide-react";

import { toastError } from "@/lib/toast";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  cleanDisplayName,
  providerKeySet,
  useAiConfig,
  useFetchProviderModels,
  useProviderModels,
  type AiConfigOut,
  type AiProvider,
  type ModelInfo,
} from "@/lib/ai-queries";

export interface AiModelSelection {
  provider: AiProvider | null;
  model: string | null;
  /**
   * Optional output dimension for the embedding model. Only meaningful when
   * the picker is rendered with `showDimensions`; only `text-embedding-3-*`
   * (and a few third-party models) honour it. `null` ⇒ the model's native
   * dimension is used.
   */
  dimensions?: number | null;
}

const DIMENSION_PRESETS = [256, 512, 1024, 1536, 3072] as const;

const PROVIDER_LABELS: Record<AiProvider, string> = {
  anthropic: "Anthropic",
  openai: "OpenAI",
  unigpt: "UniGPT",
  custom: "Custom",
};

const ALL_PROVIDERS: AiProvider[] = ["anthropic", "openai", "unigpt", "custom"];

function isProviderConfigured(provider: AiProvider, cfg: AiConfigOut): boolean {
  // Masked config: a stored key (key_set) plus a base URL when the provider
  // needs one. The platform key flows to the proxy server-side.
  if (!providerKeySet(cfg, provider)) return false;
  if ((provider === "unigpt" || provider === "custom") && !cfg[`${provider}_base_url`]) {
    return false;
  }
  return true;
}

interface AiModelPickerProps {
  title: string;
  description?: string | null;
  value: AiModelSelection;
  onChange: (next: AiModelSelection) => void;
  /** Providers to expose. Defaults to all four. Use to hide e.g. Anthropic for embeddings. */
  allowProviders?: AiProvider[];
  /** When true, prefer models whose id matches /embed/i in the dropdown (no hard filter). */
  preferEmbeddingModels?: boolean;
  /**
   * When true, render an extra "Dimensions" number input + preset chips below
   * the provider/model row. The selected value is stored on
   * `AiModelSelection.dimensions`. Leave blank to use the model's native dim.
   */
  showDimensions?: boolean;
}

export function AiModelPicker({
  title,
  description,
  value,
  onChange,
  allowProviders = ALL_PROVIDERS,
  preferEmbeddingModels = false,
  showDimensions = false,
}: AiModelPickerProps) {
  const { data: aiCfg } = useAiConfig();
  const fetchModels = useFetchProviderModels();
  const qc = useQueryClient();
  const [refreshingProvider, setRefreshingProvider] = useState<AiProvider | null>(null);

  const configured = useMemo(() => {
    const s = new Set<AiProvider>();
    if (!aiCfg) return s;
    for (const p of allowProviders) {
      if (isProviderConfigured(p, aiCfg)) s.add(p);
    }
    return s;
  }, [aiCfg, allowProviders]);

  const providerReady =
    value.provider !== null && configured.has(value.provider);
  const modelsQuery = useProviderModels(value.provider, providerReady);
  const fetchingProvider =
    refreshingProvider ?? (modelsQuery.isFetching ? value.provider : null);

  const onPickProvider = (p: AiProvider | null) => {
    onChange({ provider: p, model: null, dimensions: value.dimensions ?? null });
  };

  const onDimensionsChange = (raw: string) => {
    const trimmed = raw.trim();
    if (trimmed === "") {
      onChange({ ...value, dimensions: null });
      return;
    }
    const n = Number(trimmed);
    if (!Number.isFinite(n) || n <= 0) return;
    onChange({ ...value, dimensions: Math.floor(n) });
  };

  const onRefreshModels = async (p: AiProvider) => {
    setRefreshingProvider(p);
    try {
      const res = await fetchModels.mutateAsync(p);
      qc.setQueryData(["ai", "models", p], res);
    } catch (e) {
      toastError(`Could not fetch models: ${(e as Error).message}`);
    } finally {
      setRefreshingProvider(null);
    }
  };

  const allModels: ModelInfo[] = modelsQuery.data?.models ?? [];

  const displayedModels = useMemo(() => {
    if (!preferEmbeddingModels) return allModels;
    const onlyEmbed = allModels.filter((m) => /embed/i.test(m.id));
    return onlyEmbed.length > 0 ? onlyEmbed : allModels;
  }, [allModels, preferEmbeddingModels]);

  const savedNotInList =
    value.model !== null && !displayedModels.some((m) => m.id === value.model);

  return (
    <div className="space-y-2">
      <div>
        <Label className="text-sm">{title}</Label>
        {description && (
          <p className="text-xs text-muted-foreground mt-0.5">{description}</p>
        )}
      </div>

      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label className="text-xs text-muted-foreground">Provider</Label>
          <Select
            value={value.provider ?? "__none"}
            onValueChange={(v) =>
              onPickProvider(v === "__none" ? null : (v as AiProvider))
            }
          >
            <SelectTrigger className="text-xs">
              <SelectValue placeholder="Choose a provider" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__none">None</SelectItem>
              {allowProviders.map((p) => {
                const isReady = configured.has(p);
                return (
                  <SelectItem key={p} value={p} disabled={!isReady}>
                    {PROVIDER_LABELS[p]}
                    {!isReady && (
                      <span className="text-muted-foreground"> - no key in Settings → AI</span>
                    )}
                  </SelectItem>
                );
              })}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <Label className="text-xs text-muted-foreground">Model</Label>
            {value.provider && configured.has(value.provider) && (
              <button
                type="button"
                onClick={() => onRefreshModels(value.provider!)}
                disabled={fetchingProvider === value.provider}
                className="cursor-pointer text-xs text-muted-foreground hover:text-foreground inline-flex items-center gap-1 disabled:opacity-50"
              >
                {fetchingProvider === value.provider ? (
                  <Loader2 className="h-3 w-3 animate-spin" />
                ) : (
                  <RefreshCw className="h-3 w-3" />
                )}
                Refresh
              </button>
            )}
          </div>
          <Select
            value={value.model ?? "__none"}
            onValueChange={(v) =>
              onChange({ ...value, model: v === "__none" ? null : v })
            }
            disabled={!value.provider || !configured.has(value.provider)}
          >
            <SelectTrigger className="text-xs">
              <SelectValue
                placeholder={
                  value.provider
                    ? configured.has(value.provider)
                      ? "Choose a model"
                      : "Configure provider in Settings → AI first"
                    : "Pick a provider first"
                }
              />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__none">None</SelectItem>
              {savedNotInList && value.model && (
                <SelectItem value={value.model}>
                  {cleanDisplayName(value.model)}{" "}
                  <span className="text-muted-foreground">(saved)</span>
                </SelectItem>
              )}
              {displayedModels.map((m) => (
                <SelectItem key={m.id} value={m.id}>
                  {m.display_name ?? cleanDisplayName(m.id)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {showDimensions && (
        <div className="space-y-1.5 pt-1">
          <Label htmlFor="ai-embedding-dimensions" className="text-xs text-muted-foreground">
            Dimensions
          </Label>
          <div className="flex flex-wrap items-center gap-2">
            <Input
              id="ai-embedding-dimensions"
              type="number"
              inputMode="numeric"
              min={1}
              max={4096}
              step={1}
              value={value.dimensions ?? ""}
              onChange={(e) => onDimensionsChange(e.target.value)}
              placeholder="Native (leave blank)"
              className="h-8 w-40 text-xs"
            />
            <div className="flex flex-wrap gap-1">
              {DIMENSION_PRESETS.map((d) => {
                const active = value.dimensions === d;
                return (
                  <button
                    key={d}
                    type="button"
                    onClick={() => onChange({ ...value, dimensions: d })}
                    className={
                      "cursor-pointer rounded-md border px-2 py-0.5 text-[11px] transition-colors " +
                      (active
                        ? "border-foreground bg-foreground text-background"
                        : "border-border bg-muted/40 text-muted-foreground hover:text-foreground")
                    }
                  >
                    {d}
                  </button>
                );
              })}
              {value.dimensions !== null && value.dimensions !== undefined && (
                <button
                  type="button"
                  onClick={() => onChange({ ...value, dimensions: null })}
                  className="cursor-pointer rounded-md border border-border bg-muted/40 px-2 py-0.5 text-[11px] text-muted-foreground hover:text-foreground"
                >
                  Clear
                </button>
              )}
            </div>
          </div>
          <p className="text-[11px] text-muted-foreground">
            Used to size the Pinecone vector index. For the official OpenAI
            provider this is also sent to the API to truncate{" "}
            <code className="rounded bg-muted px-1 py-0.5">text-embedding-3-*</code> output. For
            UniGPT / Custom providers it&apos;s recorded locally only - most
            OpenAI-compatible proxies reject the{" "}
            <code className="rounded bg-muted px-1 py-0.5">dimensions</code> parameter, so set this
            to whatever your model natively outputs. Leave blank to default to 1536. Changing this
            requires recreating the Pinecone index.
          </p>
        </div>
      )}
    </div>
  );
}

export function AiKeysBanner() {
  return (
    <div className="flex items-start gap-2.5 rounded-md border border-border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
      <span>
        API keys are managed centrally in{" "}
        <Link
          href="/settings/ai"
          className="underline underline-offset-2 hover:text-foreground inline-flex items-center gap-0.5"
        >
          Settings → AI
          <ExternalLink className="h-3 w-3" />
        </Link>
        . Pick the providers and models this module should use below.
      </span>
    </div>
  );
}
