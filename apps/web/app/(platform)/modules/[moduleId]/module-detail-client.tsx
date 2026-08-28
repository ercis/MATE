"use client";

import { useEffect, useState } from "react";
import { FileBox, Lock, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { toastError } from "@/lib/toast";
import { useProgressRouter } from "@/lib/use-progress-router";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ActionButton } from "@/components/ui/action-button";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import {
  ModuleConfigForm,
  type ConfigSchema,
} from "@/components/modules/module-config-form";
import { ModuleAboutInfo } from "@/components/modules/module-about";
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
import { ApiError } from "@/lib/api";
import { EmptyState } from "@/components/empty-state";
import { PageContainer } from "@/components/page";
import { ModuleLogsTail } from "@/components/settings/module-logs-tail";
import { ModelStoreCard } from "@/components/settings/model-store-card";
import {
  AiKeysBanner,
  AiModelPicker,
  type AiModelSelection,
} from "@/components/ai/ai-model-picker";
import {
  ModuleOpenAiCard,
  EMPTY_MODULE_AI_DRAFT,
  readModuleAiDraft,
  type ModuleAiDraft,
} from "@/components/ai/module-openai-card";
import type { AiProvider } from "@/lib/ai-queries";
import {
  useModuleConfig,
  useModuleManifest,
  useRecreateModuleIndex,
  useUninstallModule,
  useUpdateModuleConfig,
  type AiModelsManifest,
  type ModelStoreManifest,
} from "@/lib/queries";
import type { ManifestArtifact, ManifestSource } from "@/lib/api-types";

interface AiConfigDraft {
  llm: AiModelSelection;
  embedding: AiModelSelection;
}

const EMPTY_AI_DRAFT: AiConfigDraft = {
  llm: { provider: null, model: null },
  embedding: { provider: null, model: null, dimensions: null },
};

const EMBEDDING_PROVIDERS: AiProvider[] = ["openai", "unigpt", "custom"];

function readAiDraft(cfg: Record<string, unknown>): AiConfigDraft {
  const ai = (cfg.ai as Record<string, unknown> | undefined) ?? {};
  const llm = (ai.llm as Partial<AiModelSelection> | undefined) ?? {};
  const embedding = (ai.embedding as Partial<AiModelSelection> | undefined) ?? {};
  const rawDim = embedding.dimensions;
  const dimensions =
    typeof rawDim === "number" && Number.isFinite(rawDim) && rawDim > 0
      ? Math.floor(rawDim)
      : null;
  return {
    llm: {
      provider: (llm.provider as AiProvider | null | undefined) ?? null,
      model: (llm.model as string | null | undefined) ?? null,
    },
    embedding: {
      provider: (embedding.provider as AiProvider | null | undefined) ?? null,
      model: (embedding.model as string | null | undefined) ?? null,
      dimensions,
    },
  };
}

export function ModuleDetailClient({ moduleId }: { moduleId: string }) {
  const router = useProgressRouter();
  const { data: cfg } = useModuleConfig(moduleId);
  const { data: manifest, isLoading: manifestLoading, isError: manifestError } =
    useModuleManifest(moduleId);
  const uninstall = useUninstallModule();
  const update = useUpdateModuleConfig();
  const recreateIndex = useRecreateModuleIndex(moduleId);

  // Per-card admin locks: each settings card (config / ai / model) can be
  // locked independently, so each goes read-only on its own. `enabled` is
  // per-user install state and stays editable regardless. controlled_by_admin
  // is the back-compat "every card locked" flag.
  const controlledCards = cfg?.controlled_cards ?? {};
  const configLocked = controlledCards.config ?? false;
  const aiLocked = controlledCards.ai ?? false;
  const modelLocked = controlledCards.model ?? false;
  const anyLocked = configLocked || aiLocked || modelLocked;
  const allLocked = cfg?.controlled_by_admin ?? false;

  const schema = (manifest?.config_schema as ConfigSchema | undefined) ?? null;
  const properties = schema?.properties ?? {};
  const hasSchema = Object.keys(properties).length > 0;

  const aiManifest: AiModelsManifest | null = manifest?.ai_models ?? null;
  const hasAiModels = Boolean(aiManifest && (aiManifest.llm || aiManifest.embedding));

  // Optional uploadable-model store (e.g. cv4cdd). Selection is persisted into
  // the module config under `config_key` (default "model").
  const modelStore: ModelStoreManifest | null = manifest?.model_store ?? null;
  const modelConfigKey = modelStore?.config_key ?? "model";
  // Self-hosted modules own their OpenAI key (isolated from Settings → AI) and
  // render the module's own card instead of the platform-keyed picker.
  const selfHosted = Boolean(aiManifest?.self_hosted);

  const [enabled, setEnabled] = useState<boolean>(true);
  useEffect(() => {
    if (cfg !== undefined) setEnabled(cfg.enabled);
  }, [cfg]);

  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [aiDraft, setAiDraft] = useState<AiConfigDraft>(EMPTY_AI_DRAFT);
  const [moduleAiDraft, setModuleAiDraft] = useState<ModuleAiDraft>(EMPTY_MODULE_AI_DRAFT);
  const [savedApiKey, setSavedApiKey] = useState<string>("");
  useEffect(() => {
    if (cfg !== undefined) {
      const c = cfg.config ?? {};
      setDraft(c);
      setAiDraft(readAiDraft(c));
      const mAi = readModuleAiDraft(c);
      setModuleAiDraft(mAi);
      setSavedApiKey(mAi.api_key);
    }
  }, [cfg]);

  const m = manifest
    ? {
        id: manifest.id as string,
        name: manifest.name as string,
        version: manifest.version as string,
        category: manifest.category as string,
        description: (manifest.description as string | null) ?? null,
        about: (manifest.about as string | null) ?? null,
        source: (manifest.source as ManifestSource[] | undefined) ?? [],
        artifacts: (manifest.artifacts as ManifestArtifact[] | undefined) ?? [],
        license: (manifest.license as string | null) ?? null,
        provides: (manifest.provides as string[]) ?? [],
        consumes: (manifest.consumes as string[]) ?? [],
      }
    : null;

  const composeConfig = (base: Record<string, unknown>): Record<string, unknown> => {
    if (!hasAiModels) return base;
    if (selfHosted) {
      return {
        ...base,
        ai: {
          api_key: moduleAiDraft.api_key,
          llm_model: moduleAiDraft.llm_model,
          embedding_model: moduleAiDraft.embedding_model,
          embedding_dimensions: moduleAiDraft.embedding_dimensions,
        },
      };
    }
    return { ...base, ai: { llm: aiDraft.llm, embedding: aiDraft.embedding } };
  };

  const onToggleEnabled = async (val: boolean) => {
    setEnabled(val);
    try {
      await update.mutateAsync({
        id: moduleId,
        config: composeConfig(draft),
        enabled: val,
      });
      toast.success(val ? `${m?.name ?? moduleId} enabled` : `${m?.name ?? moduleId} disabled`);
    } catch (e) {
      setEnabled(!val);
      toastError(
        e instanceof ApiError && e.status === 403
          ? "This module is controlled by your administrator."
          : "Failed to update module",
      );
    }
  };

  const onSaveConfig = async () => {
    // No client-side lock guard: the server strips any admin-locked card's
    // slice from the payload, so saving from an unlocked card still works.
    try {
      await update.mutateAsync({
        id: moduleId,
        config: composeConfig(draft),
        enabled,
      });
      toast.success("Configuration saved");
      if (selfHosted) setSavedApiKey(moduleAiDraft.api_key);
    } catch (e) {
      toastError(
        e instanceof ApiError && e.status === 403
          ? "This module is controlled by your administrator."
          : "Failed to save configuration",
      );
    }
  };

  const onSelectModel = async (name: string) => {
    if (modelLocked) return;
    const next = { ...draft, [modelConfigKey]: name };
    setDraft(next);
    await update.mutateAsync({
      id: moduleId,
      config: composeConfig(next),
      enabled,
    });
  };

  const onUninstall = async () => {
    try {
      await uninstall.mutateAsync(moduleId);
      toast.success(`Uninstalled ${m?.name ?? moduleId}`);
      router.push("/modules");
    } catch (err: unknown) {
      toastError(`Uninstall failed: ${(err as Error).message}`);
    }
  };

  if (manifestLoading) {
    return (
      <PageContainer className="space-y-3">
        <Skeleton className="h-6 w-48" />
        <Skeleton className="h-32 w-full" />
      </PageContainer>
    );
  }
  if (manifestError || !m) {
    return (
      <EmptyState
        icon={FileBox}
        title={`Module "${moduleId}" not found`}
        description="It may have been uninstalled or failed to load."
      />
    );
  }

  const cfgLoading = cfg === undefined;

  return (
    <PageContainer className="space-y-4">
      {anyLocked && (
        <div className="flex items-start gap-2.5 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          <Lock className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            {allLocked
              ? "This module is controlled by your administrator. Its settings cannot be changed."
              : "Some settings on this module are controlled by your administrator and are read-only."}
          </span>
        </div>
      )}

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between gap-4">
            <CardTitle className="text-base flex flex-wrap items-center gap-2">
              {m.name}
              <Badge variant="secondary" className="h-5 px-2 py-0 text-[9px] font-medium uppercase tracking-wide">
                {m.category.replace("_", " ")}
              </Badge>
              <span className="text-xs font-normal text-muted-foreground">{m.version}</span>
              <ModuleAboutInfo
                name={m.name}
                description={m.description}
                about={m.about}
                sources={m.source}
                artifacts={m.artifacts}
                license={m.license}
                version={m.version}
              />
            </CardTitle>
            <div className="flex items-center gap-2 shrink-0">
              {cfgLoading ? (
                <Skeleton className="h-6 w-24" />
              ) : (
                <>
                  <Label htmlFor="module-enabled" className="text-sm">
                    {enabled ? "Enabled" : "Disabled"}
                  </Label>
                  <Switch
                    id="module-enabled"
                    checked={enabled}
                    onCheckedChange={onToggleEnabled}
                    disabled={update.isPending}
                  />
                </>
              )}
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4 text-sm">
          {m.description && <p className="text-muted-foreground">{m.description}</p>}
          <Section label="Provides" items={m.provides.length ? m.provides : ["–"]} />
          <Section label="Consumes" items={m.consumes.length ? m.consumes : ["–"]} />
        </CardContent>
      </Card>

      {hasAiModels && aiManifest && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">AI models</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {cfgLoading ? (
              <Skeleton className="h-20 w-full" />
            ) : selfHosted ? (
              <ModuleOpenAiCard
                moduleId={moduleId}
                savedApiKey={savedApiKey}
                llmSlot={aiManifest.llm}
                embeddingSlot={aiManifest.embedding}
                value={moduleAiDraft}
                onChange={setModuleAiDraft}
                disabled={aiLocked}
              />
            ) : (
              <>
                <AiKeysBanner />
                {aiManifest.llm && (
                  <AiModelPicker
                    title={aiManifest.llm.title}
                    description={aiManifest.llm.description}
                    value={aiDraft.llm}
                    onChange={(next) => setAiDraft((d) => ({ ...d, llm: next }))}
                    disabled={aiLocked}
                  />
                )}
                {aiManifest.embedding && (
                  <AiModelPicker
                    title={aiManifest.embedding.title}
                    description={aiManifest.embedding.description}
                    value={aiDraft.embedding}
                    onChange={(next) => setAiDraft((d) => ({ ...d, embedding: next }))}
                    allowProviders={EMBEDDING_PROVIDERS}
                    preferEmbeddingModels
                    showDimensions
                    disabled={aiLocked}
                  />
                )}
              </>
            )}
            <Separator />
            <div className="flex items-center justify-between gap-2">
              {aiManifest.embedding ? (
                <AlertDialog>
                  <AlertDialogTrigger asChild>
                    <Button
                      size="sm"
                      variant="outline"
                      className="cursor-pointer text-destructive hover:bg-destructive/10"
                      disabled={recreateIndex.isPending || aiLocked}
                    >
                      Recreate Pinecone index
                    </Button>
                  </AlertDialogTrigger>
                  <AlertDialogContent>
                    <AlertDialogHeader>
                      <AlertDialogTitle>Recreate the Pinecone index?</AlertDialogTitle>
                      <AlertDialogDescription>
                        This deletes the index and creates a fresh one using the dimension
                        currently configured above. <strong>All previously ingested
                        document vectors will be lost</strong> - you&apos;ll need to re-run
                        ingestion afterwards. Save any pending AI-model changes first.
                      </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                      <AlertDialogCancel className="cursor-pointer">Cancel</AlertDialogCancel>
                      <AlertDialogAction
                        onClick={async () => {
                          try {
                            const res = await recreateIndex.mutateAsync();
                            toast.success(
                              `Recreated "${res.index_name}" (dim ${res.dimension})`,
                            );
                          } catch (e) {
                            toastError(
                              `Failed to recreate index: ${(e as Error).message}`,
                            );
                          }
                        }}
                        className="cursor-pointer bg-destructive text-destructive-foreground hover:bg-destructive/90"
                      >
                        Delete & recreate
                      </AlertDialogAction>
                    </AlertDialogFooter>
                  </AlertDialogContent>
                </AlertDialog>
              ) : (
                <span />
              )}
              <ActionButton
                size="sm"
                onClick={onSaveConfig}
                isPending={update.isPending}
                isSuccess={update.isSuccess}
                disabled={cfgLoading || aiLocked}
                className="cursor-pointer"
              >
                Save AI models
              </ActionButton>
            </div>
          </CardContent>
        </Card>
      )}

      {modelStore && !cfgLoading && (
        <ModelStoreCard
          moduleId={moduleId}
          store={modelStore}
          selected={
            typeof draft[modelConfigKey] === "string"
              ? (draft[modelConfigKey] as string)
              : null
          }
          onSelect={onSelectModel}
          saving={update.isPending}
        />
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Configuration</CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">
          {hasSchema || hasAiModels ? (
            cfgLoading ? (
              <Skeleton className="h-24 w-full" />
            ) : (
              <>
                {hasSchema && (
                  <div className={configLocked ? "opacity-60" : undefined}>
                    <ModuleConfigForm
                      properties={properties}
                      values={draft}
                      onChange={(key, val) => setDraft((d) => ({ ...d, [key]: val }))}
                      disabled={configLocked}
                    />
                  </div>
                )}
                {hasSchema && !configLocked && <Separator />}
                {!configLocked && (
                  <div className="flex justify-end">
                    <ActionButton
                      size="sm"
                      onClick={onSaveConfig}
                      isPending={update.isPending}
                      isSuccess={update.isSuccess}
                      className="cursor-pointer"
                    >
                      Save configuration
                    </ActionButton>
                  </div>
                )}
              </>
            )
          ) : (
            <p className="text-xs text-muted-foreground">
              This module has no configurable parameters.
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Logs</CardTitle>
        </CardHeader>
        <CardContent>
          <ModuleLogsTail moduleId={m.id} />
        </CardContent>
      </Card>

      <Card className="border-destructive/30">
        <CardHeader>
          <CardTitle className="text-base">Danger zone</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <p className="text-muted-foreground">
            Removes the module from your account. Default modules can be brought
            back with <span className="font-medium text-foreground">Restore defaults</span>;
            custom uploads are deleted from disk once no one else has them installed.
          </p>
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button
                variant="outline"
                className="cursor-pointer gap-2 text-destructive hover:bg-destructive/10"
              >
                <Trash2 className="h-3.5 w-3.5" />
                Uninstall
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Uninstall {m.name}?</AlertDialogTitle>
                <AlertDialogDescription>
                  This removes <code>{m.id}</code> from your installed modules and
                  unmounts it for you. Your event-log data and other users are
                  unaffected.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel className="cursor-pointer">Cancel</AlertDialogCancel>
                <AlertDialogAction
                  onClick={onUninstall}
                  className="cursor-pointer bg-destructive text-destructive-foreground hover:bg-destructive/90"
                >
                  Uninstall
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </CardContent>
      </Card>
    </PageContainer>
  );
}

function Section({ label, items }: { label: string; items: string[] }) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="mt-1 flex flex-wrap gap-1.5">
        {items.map((s) => (
          <code key={s} className="rounded bg-muted px-1.5 py-0.5 text-[11px]">
            {s}
          </code>
        ))}
      </div>
    </div>
  );
}
