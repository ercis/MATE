"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Check, Loader2 } from "lucide-react";

import { toastError } from "@/lib/toast";
import { useModuleAiCheck } from "@/lib/queries";
import { cleanDisplayName } from "@/lib/ai-queries";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export interface ModuleAiDraft {
  api_key: string;
  llm_model: string | null;
  embedding_model: string | null;
  embedding_dimensions: number | null;
}

export const EMPTY_MODULE_AI_DRAFT: ModuleAiDraft = {
  api_key: "",
  llm_model: null,
  embedding_model: null,
  embedding_dimensions: null,
};

export function readModuleAiDraft(cfg: Record<string, unknown>): ModuleAiDraft {
  const ai = (cfg.ai as Record<string, unknown> | undefined) ?? {};
  const rawDim = ai.embedding_dimensions;
  const dimensions =
    typeof rawDim === "number" && Number.isFinite(rawDim) && rawDim > 0
      ? Math.floor(rawDim)
      : null;
  return {
    api_key: typeof ai.api_key === "string" ? ai.api_key : "",
    llm_model: typeof ai.llm_model === "string" ? ai.llm_model : null,
    embedding_model: typeof ai.embedding_model === "string" ? ai.embedding_model : null,
    embedding_dimensions: dimensions,
  };
}

const DIMENSION_PRESETS = [256, 512, 1024, 1536, 3072] as const;

interface SlotInfo {
  title: string;
  description?: string | null;
}

interface ModuleOpenAiCardProps {
  moduleId: string;
  /** The saved key (used to decide whether to auto-check on mount). */
  savedApiKey: string;
  llmSlot?: SlotInfo | null;
  embeddingSlot?: SlotInfo | null;
  value: ModuleAiDraft;
  onChange: (next: ModuleAiDraft) => void;
  /** When true the whole card is read-only (admin-locked ai card). */
  disabled?: boolean;
}

export function ModuleOpenAiCard({
  moduleId,
  savedApiKey,
  llmSlot,
  embeddingSlot,
  value,
  onChange,
  disabled = false,
}: ModuleOpenAiCardProps) {
  const check = useModuleAiCheck(moduleId);
  const [models, setModels] = useState<string[] | null>(null);
  const autoCheckedRef = useRef(false);

  const runCheck = async (key: string | null) => {
    try {
      const res = await check.mutateAsync(key);
      setModels(res.models);
      return res.models;
    } catch (e) {
      toastError(`Could not verify OpenAI key: ${(e as Error).message}`);
      return null;
    }
  };

  // Auto-populate the dropdowns once on mount when a key is already saved.
  useEffect(() => {
    if (autoCheckedRef.current) return;
    if (savedApiKey.trim()) {
      autoCheckedRef.current = true;
      void runCheck(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [savedApiKey]);

  const chatModels = useMemo(() => {
    if (!models) return [];
    return models.filter((m) => !/embed/i.test(m));
  }, [models]);

  const embeddingModels = useMemo(() => {
    if (!models) return [];
    const onlyEmbed = models.filter((m) => /embed/i.test(m));
    return onlyEmbed.length > 0 ? onlyEmbed : models;
  }, [models]);

  const onDimensionsChange = (raw: string) => {
    const trimmed = raw.trim();
    if (trimmed === "") {
      onChange({ ...value, embedding_dimensions: null });
      return;
    }
    const n = Number(trimmed);
    if (!Number.isFinite(n) || n <= 0) return;
    onChange({ ...value, embedding_dimensions: Math.floor(n) });
  };

  const checked = models !== null;

  return (
    <div className="space-y-4">
      <div className="space-y-1.5">
        <Label htmlFor="cde-openai-key" className="text-sm">
          OpenAI API key
        </Label>
        <p className="text-xs text-muted-foreground">
          Stored with this module only – never shared with the platform&apos;s
          global Settings → AI. Created at platform.openai.com.
        </p>
        <div className="flex max-w-xl items-center gap-2">
          <Input
            id="cde-openai-key"
            type="password"
            autoComplete="off"
            value={value.api_key}
            onChange={(e) => onChange({ ...value, api_key: e.target.value })}
            placeholder="sk-..."
            className="font-mono text-xs"
            disabled={disabled}
          />
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="cursor-pointer shrink-0 gap-1"
            disabled={disabled || check.isPending || !value.api_key.trim()}
            onClick={() => runCheck(value.api_key.trim() || null)}
          >
            {check.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : checked ? (
              <Check className="h-3.5 w-3.5" />
            ) : null}
            Check
          </Button>
        </div>
        {checked && (
          <p className="text-[11px] text-emerald-600 dark:text-emerald-500">
            Key verified – {models!.length} models available.
          </p>
        )}
      </div>

      <ModelSelect
        title={llmSlot?.title ?? "Agent model"}
        description={llmSlot?.description}
        placeholder="Check your key to list models"
        value={value.llm_model}
        options={chatModels}
        disabled={!checked}
        readOnly={disabled}
        onChange={(m) => onChange({ ...value, llm_model: m })}
      />

      <div className="space-y-2">
        <ModelSelect
          title={embeddingSlot?.title ?? "Embedding model"}
          description={embeddingSlot?.description}
          placeholder="Check your key to list models"
          value={value.embedding_model}
          options={embeddingModels}
          disabled={!checked}
          readOnly={disabled}
          onChange={(m) => onChange({ ...value, embedding_model: m })}
        />

        <div className="space-y-1.5 pt-1">
          <Label htmlFor="cde-embedding-dimensions" className="text-xs text-muted-foreground">
            Dimensions
          </Label>
          <div className="flex flex-wrap items-center gap-2">
            <Input
              id="cde-embedding-dimensions"
              type="number"
              inputMode="numeric"
              min={1}
              max={4096}
              step={1}
              value={value.embedding_dimensions ?? ""}
              onChange={(e) => onDimensionsChange(e.target.value)}
              placeholder="Native (leave blank)"
              className="h-8 w-40 text-xs"
              disabled={disabled}
            />
            <div className="flex flex-wrap gap-1">
              {DIMENSION_PRESETS.map((d) => {
                const active = value.embedding_dimensions === d;
                return (
                  <button
                    key={d}
                    type="button"
                    disabled={disabled}
                    onClick={() => onChange({ ...value, embedding_dimensions: d })}
                    className={
                      "rounded-md border px-2 py-0.5 text-[11px] transition-colors disabled:cursor-not-allowed disabled:opacity-50 " +
                      (active
                        ? "border-foreground bg-foreground text-background"
                        : "cursor-pointer border-border bg-muted/40 text-muted-foreground hover:text-foreground")
                    }
                  >
                    {d}
                  </button>
                );
              })}
              {value.embedding_dimensions !== null && (
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => onChange({ ...value, embedding_dimensions: null })}
                  className="cursor-pointer rounded-md border border-border bg-muted/40 px-2 py-0.5 text-[11px] text-muted-foreground hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
                >
                  Clear
                </button>
              )}
            </div>
          </div>
          <p className="text-[11px] text-muted-foreground">
            Used to size the Pinecone vector index, and sent to OpenAI to truncate{" "}
            <code className="rounded bg-muted px-1 py-0.5">text-embedding-3-*</code> output. Leave
            blank to default to 1536. Changing this requires recreating the Pinecone index.
          </p>
        </div>
      </div>
    </div>
  );
}

function ModelSelect({
  title,
  description,
  placeholder,
  value,
  options,
  disabled,
  readOnly = false,
  onChange,
}: {
  title: string;
  description?: string | null;
  placeholder: string;
  value: string | null;
  options: string[];
  disabled: boolean;
  /** Hard read-only (admin lock) - overrides the saved-not-in-list escape. */
  readOnly?: boolean;
  onChange: (model: string | null) => void;
}) {
  const savedNotInList = value !== null && !options.includes(value);
  return (
    <div className="space-y-1.5">
      <Label className="text-sm">{title}</Label>
      {description && <p className="text-xs text-muted-foreground">{description}</p>}
      <Select
        value={value ?? "__none"}
        onValueChange={(v) => onChange(v === "__none" ? null : v)}
        disabled={readOnly || (disabled && !savedNotInList)}
      >
        <SelectTrigger className="text-xs">
          <SelectValue placeholder={placeholder} />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="__none">None</SelectItem>
          {savedNotInList && value && (
            <SelectItem value={value}>
              {cleanDisplayName(value)} <span className="text-muted-foreground">(saved)</span>
            </SelectItem>
          )}
          {options.map((m) => (
            <SelectItem key={m} value={m}>
              {m}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
