"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, rawFetch } from "@/lib/api";

export type GuidanceSeverity = "info" | "warning" | "critical";

export interface GuidanceFlag {
  severity: GuidanceSeverity;
  message: string;
}

export interface GuidanceBody {
  interpretation: string;
  recommended_actions: string[];
  anomaly_flags: GuidanceFlag[];
}

export interface GuidanceResponse {
  cached: boolean;
  output_hash: string;
  generated_at: number;
  model: string | null;
  provider: string | null;
  guidance: GuidanceBody;
}

export interface ImportColumnMappingResponse {
  suggestions: Partial<
    Record<
      "case_id" | "activity" | "timestamp" | "end_timestamp" | "resource" | "cost",
      string
    >
  >;
}

const KEYS = {
  module: (logId: string, moduleId: string) =>
    ["ai", "guidance", "module", logId, moduleId] as const,
  process: (logId: string) => ["ai", "guidance", "process", logId] as const,
  quality: (logId: string) => ["ai", "guidance", "import-quality", logId] as const,
};

// ── Module-level guidance ───────────────────────────────────────────────────

/** Lazy guidance fetch: gated behind `enabled: false` so it only fires when
 *  the user clicks the button (via `refetch()`). The response server-side
 *  is cache-aware, so subsequent refetches don't burn tokens unless the
 *  underlying module output has changed. */
export function useModuleGuidance(logId: string, moduleId: string) {
  return useQuery<GuidanceResponse>({
    queryKey: KEYS.module(logId, moduleId),
    queryFn: () =>
      api<GuidanceResponse>(
        `/api/v1/ai/guidance/module/${moduleId}?log_id=${encodeURIComponent(logId)}`,
        { method: "POST", json: { force: false } },
      ),
    enabled: false,
    staleTime: Infinity,
    retry: false,
  });
}

export function useRegenerateModuleGuidance(logId: string, moduleId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api<GuidanceResponse>(
        `/api/v1/ai/guidance/module/${moduleId}?log_id=${encodeURIComponent(logId)}`,
        { method: "POST", json: { force: true } },
      ),
    onSuccess: (data) => qc.setQueryData(KEYS.module(logId, moduleId), data),
  });
}

// ── Process-level guidance ─────────────────────────────────────────────────

export function useProcessGuidance(logId: string) {
  return useQuery<GuidanceResponse>({
    queryKey: KEYS.process(logId),
    queryFn: () =>
      api<GuidanceResponse>(
        `/api/v1/ai/guidance/process/${encodeURIComponent(logId)}`,
        { method: "POST", json: { force: false } },
      ),
    enabled: false,
    staleTime: Infinity,
    retry: false,
  });
}

export function useRegenerateProcessGuidance(logId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api<GuidanceResponse>(
        `/api/v1/ai/guidance/process/${encodeURIComponent(logId)}`,
        { method: "POST", json: { force: true } },
      ),
    onSuccess: (data) => qc.setQueryData(KEYS.process(logId), data),
  });
}

// ── Import-time helpers ────────────────────────────────────────────────────

export function useImportColumnMapping() {
  return useMutation({
    mutationFn: (input: { headers: string[]; sample_rows: string[][] }) =>
      api<ImportColumnMappingResponse>(`/api/v1/ai/guidance/import/column-mapping`, {
        method: "POST",
        json: input,
      }),
  });
}

export function useImportQualityGuidance(logId: string) {
  return useQuery<GuidanceResponse>({
    queryKey: KEYS.quality(logId),
    queryFn: () =>
      api<GuidanceResponse>(
        `/api/v1/ai/guidance/import/quality/${encodeURIComponent(logId)}`,
        { method: "POST", json: { force: false } },
      ),
    enabled: false,
    staleTime: Infinity,
    retry: false,
  });
}

// ── Streaming variant (interpretation only; structured tail at the end) ────

export interface StreamCallbacks {
  onDelta?: (text: string) => void;
  onFinal?: (response: GuidanceResponse) => void;
  onError?: (message: string) => void;
}

export async function streamModuleGuidance(
  logId: string,
  moduleId: string,
  callbacks: StreamCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  const res = await rawFetch(
    `/api/v1/ai/guidance/module/${moduleId}/stream?log_id=${encodeURIComponent(logId)}`,
    { method: "POST", signal },
  );
  if (!res.ok) {
    let detail: string;
    try {
      const body = await res.json();
      detail =
        typeof body?.detail === "string"
          ? body.detail
          : JSON.stringify(body?.detail ?? body);
    } catch {
      detail = await res.text();
    }
    callbacks.onError?.(detail);
    return;
  }

  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buf = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const lines = buf.split("\n");
    buf = lines.pop() ?? "";

    for (const line of lines) {
      if (!line.startsWith("data:")) continue;
      try {
        const evt = JSON.parse(line.slice(5).trim()) as {
          delta?: string;
          final?: GuidanceResponse;
          error?: string;
        };
        if (evt.delta) callbacks.onDelta?.(evt.delta);
        if (evt.final) callbacks.onFinal?.(evt.final);
        if (evt.error) callbacks.onError?.(evt.error);
      } catch {
        // skip malformed SSE chunk
      }
    }
  }
}
