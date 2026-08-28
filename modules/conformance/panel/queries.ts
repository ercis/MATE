"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";

import type {
  ConformanceResults,
  ModelsResponse,
  Technique,
  UploadResponse,
} from "./types";

const MODULE_ID = "conformance";
const STALE_TIME = 15_000;

function url(path: string, logId: string, params: Record<string, string> = {}): string {
  const q = new URLSearchParams({ log_id: logId, ...params });
  return `/api/v1/modules/${MODULE_ID}${path}?${q.toString()}`;
}

export const confKeys = {
  results: (logId: string, technique: Technique) =>
    ["modules", "conformance", "results", logId, technique] as const,
  models: (logId: string) => ["modules", "conformance", "models", logId] as const,
};

export function useConformanceResults(logId: string, technique: Technique) {
  return useQuery<ConformanceResults>({
    queryKey: confKeys.results(logId, technique),
    queryFn: () => api<ConformanceResults>(url("/results", logId, { technique })),
    enabled: Boolean(logId),
    staleTime: STALE_TIME,
  });
}

/** Results for the module's configured default technique - used by dashboard
 *  widgets that have no technique toggle. */
export function useConformanceResultsAuto(logId: string) {
  return useQuery<ConformanceResults>({
    queryKey: ["modules", "conformance", "results", logId, "auto"],
    queryFn: () => api<ConformanceResults>(url("/results", logId)),
    enabled: Boolean(logId),
    staleTime: STALE_TIME,
  });
}

export function useConformanceModels(logId: string) {
  return useQuery<ModelsResponse>({
    queryKey: confKeys.models(logId),
    queryFn: () => api<ModelsResponse>(url("/models", logId)),
    enabled: Boolean(logId),
    staleTime: STALE_TIME,
  });
}

export function useUploadModel(logId: string) {
  const qc = useQueryClient();
  return useMutation<UploadResponse, Error, File>({
    mutationFn: async (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return api<UploadResponse>(url("/model", logId), { method: "POST", body: form });
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: confKeys.models(logId) });
      void qc.invalidateQueries({ queryKey: ["modules", "conformance", "results", logId] });
    },
  });
}

export function useDeleteModel(logId: string) {
  const qc = useQueryClient();
  return useMutation<{ deleted: string; active: string | null }, Error, string>({
    mutationFn: (name: string) =>
      api(
        `/api/v1/modules/${MODULE_ID}/model/${encodeURIComponent(name)}?log_id=${encodeURIComponent(logId)}`,
        { method: "DELETE" },
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: confKeys.models(logId) });
      void qc.invalidateQueries({ queryKey: ["modules", "conformance", "results", logId] });
    },
  });
}

export function useActivateModel(logId: string) {
  const qc = useQueryClient();
  return useMutation<{ active: string }, Error, string>({
    mutationFn: (name: string) =>
      api(
        `/api/v1/modules/${MODULE_ID}/model/${encodeURIComponent(name)}/activate?log_id=${encodeURIComponent(logId)}`,
        { method: "POST" },
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: confKeys.models(logId) });
      void qc.invalidateQueries({ queryKey: ["modules", "conformance", "results", logId] });
    },
  });
}

/** POST /run returns a `{job_id}` (the route stacks `@job`). */
export function useRunConformance(logId: string) {
  return useMutation<{ job_id: string }, Error, Technique>({
    mutationFn: (technique: Technique) =>
      api<{ job_id: string }>(url("/run", logId, { technique }), { method: "POST" }),
  });
}
