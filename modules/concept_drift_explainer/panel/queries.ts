"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";

const MODULE_ID = "concept_drift_explainer";

function url(path: string, logId: string): string {
  const q = new URLSearchParams({ log_id: logId });
  return `/api/v1/modules/${MODULE_ID}${path}?${q.toString()}`;
}

export interface CdeDrift {
  drift_key: string;
  type: string;
  start_timestamp: string;
  end_timestamp: string;
  start_window: number;
  end_window: number;
  confidence: number;
  start_activity: string;
  end_activity: string;
}

export interface CdeDriftsResponse {
  kind: "cde_drifts";
  drifts: CdeDrift[];
  ran: boolean;
  n_windows: number;
}

export interface CdeDocument {
  name: string;
  size_bytes: number;
  timestamp: number | null;
  indexable: boolean;
}

export interface CdeDocumentsResponse {
  documents: CdeDocument[];
}

export interface CdeFranzoiClassification {
  full_path: string;
  reasoning: string;
}

export interface CdeContextSnippet {
  snippet_text: string;
  source_document: string;
  timestamp: number;
  score?: number;
  semantic_specificity?: number;
  priority_score?: number;
  source_type?: string;
  support_only?: boolean;
  classifications?: CdeFranzoiClassification[];
}

export interface CdeRankedCause {
  cause_description: string;
  evidence_snippet: string;
  source_document: string;
  context_category: string;
  confidence_score: number;
}

export interface CdeExplanation {
  summary: string;
  ranked_causes: CdeRankedCause[];
}

export interface CdeStoredExplanation {
  drift_info: Record<string, unknown>;
  drift_phrase: string;
  explanation: CdeExplanation;
  reranked_context_snippets: CdeContextSnippet[];
  supporting_context: CdeContextSnippet[];
}

export interface CdeExplanationsResponse {
  explanations: Record<string, CdeStoredExplanation>;
}

export interface CdeChatState {
  chat_history: [string, string][];
}

export const cdeKeys = {
  drifts: (logId: string) => ["modules", MODULE_ID, "drifts", logId] as const,
  documents: (logId: string) => ["modules", MODULE_ID, "documents", logId] as const,
  explanations: (logId: string) =>
    ["modules", MODULE_ID, "explanations", logId] as const,
  chat: (logId: string, driftKey: string) =>
    ["modules", MODULE_ID, "chat", logId, driftKey] as const,
};

// ── reads ───────────────────────────────────────────────────────────────────

export function useCdeDrifts(logId: string) {
  return useQuery<CdeDriftsResponse>({
    queryKey: cdeKeys.drifts(logId),
    queryFn: () => api<CdeDriftsResponse>(url("/drifts", logId)),
    enabled: Boolean(logId),
    staleTime: 10_000,
  });
}

export function useCdeDocuments(logId: string) {
  return useQuery<CdeDocumentsResponse>({
    queryKey: cdeKeys.documents(logId),
    queryFn: () => api<CdeDocumentsResponse>(url("/documents", logId)),
    enabled: Boolean(logId),
    staleTime: 10_000,
  });
}

export function useCdeExplanations(logId: string) {
  return useQuery<CdeExplanationsResponse>({
    queryKey: cdeKeys.explanations(logId),
    queryFn: () => api<CdeExplanationsResponse>(url("/explanations", logId)),
    enabled: Boolean(logId),
    staleTime: 30_000,
  });
}

// ── mutations ───────────────────────────────────────────────────────────────

export function useUploadDocument(logId: string) {
  const qc = useQueryClient();
  return useMutation<{ name: string; size_bytes: number }, Error, File>({
    mutationFn: async (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return api(url("/documents", logId), { method: "POST", body: form });
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: cdeKeys.documents(logId) });
    },
  });
}

export function useDeleteDocument(logId: string) {
  const qc = useQueryClient();
  return useMutation<{ deleted: string }, Error, string>({
    mutationFn: (name: string) =>
      api(
        `/api/v1/modules/${MODULE_ID}/documents/${encodeURIComponent(
          name,
        )}?log_id=${encodeURIComponent(logId)}`,
        { method: "DELETE" },
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: cdeKeys.documents(logId) });
    },
  });
}

export function useRunIngest(logId: string) {
  const qc = useQueryClient();
  return useMutation<{ job_id: string }, Error, void>({
    mutationFn: () => api(url("/ingest", logId), { method: "POST" }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: cdeKeys.documents(logId) });
    },
  });
}

export function useRunExplain(logId: string) {
  // Returns `{ job_id }` the moment the pipeline job is *queued*; the caller
  // follows the job to completion (see panel/index.tsx) and invalidates
  // `cdeKeys.explanations` then — invalidating here would refetch the stale
  // pre-run cache.
  return useMutation<{ job_id: string }, Error, { drift_key: string }>({
    mutationFn: (body) =>
      api(url("/explain", logId), { method: "POST", json: body }),
  });
}

export function useSendChat(logId: string) {
  const qc = useQueryClient();
  return useMutation<
    CdeChatState,
    Error,
    { drift_key: string; user_question: string }
  >({
    mutationFn: (body) =>
      api(url("/chat", logId), { method: "POST", json: body }),
    onSuccess: (data, variables) => {
      qc.setQueryData(
        cdeKeys.chat(logId, variables.drift_key),
        data,
      );
    },
  });
}
