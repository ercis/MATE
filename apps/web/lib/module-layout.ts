"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";

/**
 * Per-(user, log, module) widget layout persistence (§7.7).
 *
 * Backed by the `module_layouts` SQLite table on the API side. The shape of
 * the layout JSON is owned by the module itself – typically a
 * `react-grid-layout` array of `{i, x, y, w, h}` once a module adopts
 * widget composition, but the platform stores it opaquely so module
 * authors can evolve their schema without server churn.
 */

interface ModuleLayoutResponse {
  layout: Record<string, unknown>;
}

const STALE_TIME = 30_000;

export function useModuleLayout(moduleId: string, logId: string) {
  return useQuery<ModuleLayoutResponse>({
    queryKey: ["modules", "layout", moduleId, logId],
    queryFn: () =>
      api<ModuleLayoutResponse>(
        `/api/v1/modules/${encodeURIComponent(moduleId)}/layout?log_id=${encodeURIComponent(logId)}`,
      ),
    enabled: Boolean(moduleId && logId),
    staleTime: STALE_TIME,
  });
}

export function useSaveModuleLayout(moduleId: string, logId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (layout: Record<string, unknown>) =>
      api<ModuleLayoutResponse>(
        `/api/v1/modules/${encodeURIComponent(moduleId)}/layout?log_id=${encodeURIComponent(logId)}`,
        { method: "PUT", json: { layout } },
      ),
    onSuccess: (data) => {
      qc.setQueryData(["modules", "layout", moduleId, logId], data);
    },
  });
}
