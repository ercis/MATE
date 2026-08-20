"use client";

import { Skeleton } from "@/components/ui/skeleton";
import { AiSettingsEditor } from "@/components/ai/ai-settings-editor";
import {
  useAiConfig,
  useFetchProviderModels,
  usePricingCatalog,
  useUpdateAiConfig,
} from "@/lib/ai-queries";

export default function AiSettingsPage() {
  const { data: stored, isLoading, isError, error } = useAiConfig();
  const update = useUpdateAiConfig();
  const fetchModels = useFetchProviderModels();
  const { data: pricing } = usePricingCatalog();

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-64 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }
  if (isError || !stored) {
    return (
      <p className="text-sm text-destructive">
        Could not load AI settings: {(error as Error)?.message ?? "unknown"}
      </p>
    );
  }

  return (
    <AiSettingsEditor
      variant="user"
      stored={stored}
      pricing={pricing}
      saving={update.isPending}
      controlled={stored.controlled_by_admin}
      onSave={(cfg) => update.mutateAsync(cfg)}
      onFetchModels={(provider) => fetchModels.mutateAsync(provider)}
    />
  );
}
