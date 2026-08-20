"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import {
  queryKeys,
  eventLogsListPath,
  modulesListPath,
  type OcelListParams,
  type EventsListParams,
  type VariantsListParams,
} from "@/lib/query-keys";
import type {
  ActiveFilterResult,
  ActivitiesPage,
  BulkFillBody,
  BulkFillResult,
  CellPatch,
  CellPatchResult,
  ColumnValuesPage,
  DataQuality,
  EventEditsPage,
  EventLogCreateResponse,
  EventLogDetail,
  EventLogSummary,
  EventLogUpdatePayload,
  EventsPage,
  FilterEntry,
  FolderSummary,
  JobDetail,
  ModuleSummary,
  OcelEventsPage,
  OcelObjectsPage,
  OcelObjectTypeEntry,
  OcelOverview,
  OcelRelationsPage,
  RemapColumnRoles,
  ReorderItem,
  VariantCasesPage,
  VariantDetail,
  VariantsPage,
} from "@/lib/api-types";

// `queryKeys`, the list-param types, and prefetch path builders live in the
// pure, server-safe `lib/query-keys.ts` so `lib/prefetch.ts` (RSC) can share
// them without importing this `"use client"` module. Re-exported here for the
// existing `@/lib/queries` importers (jobs-provider, watched-queries, tabs).
export { queryKeys };
export type { OcelListParams, EventsListParams, VariantsListParams };

/** Matches only event-log *list* caches (key `["event-logs", params]`), never
 *  detail or sub-resource keys – so optimistic list edits don't touch them. */
function isEventLogListKey(queryKey: readonly unknown[]): boolean {
  return (
    queryKey[0] === "event-logs" &&
    queryKey.length === 2 &&
    typeof queryKey[1] === "object"
  );
}

function eventsPath(logId: string, params: EventsListParams): string {
  const qs = new URLSearchParams();
  if (params.offset !== undefined) qs.set("offset", String(params.offset));
  if (params.limit !== undefined) qs.set("limit", String(params.limit));
  if (params.sort) qs.set("sort", params.sort);
  if (params.filter && params.filter.length > 0) qs.set("filter", JSON.stringify(params.filter));
  if (params.q) qs.set("q", params.q);
  if (params.missing_only) qs.set("missing_only", "true");
  if (params.case_id) qs.set("case_id", params.case_id);
  return `/api/v1/event-logs/${logId}/events${qs.toString() ? `?${qs}` : ""}`;
}

function variantsPath(logId: string, params: VariantsListParams): string {
  const qs = new URLSearchParams();
  if (params.offset !== undefined) qs.set("offset", String(params.offset));
  if (params.limit !== undefined) qs.set("limit", String(params.limit));
  if (params.sort) qs.set("sort", params.sort);
  if (params.activity_contains) qs.set("activity_contains", params.activity_contains);
  if (params.min_case_count !== undefined) qs.set("min_case_count", String(params.min_case_count));
  return `/api/v1/event-logs/${logId}/variants${qs.toString() ? `?${qs}` : ""}`;
}

export function useEventLogs(params: { q?: string; status?: string } = {}) {
  return useQuery({
    queryKey: [...queryKeys.eventLogs(), params],
    queryFn: () => api<EventLogSummary[]>(eventLogsListPath(params)),
    // Keep the current rows on screen while a search/status change refetches,
    // so the table never blanks back to a skeleton mid-typing.
    placeholderData: (prev) => prev,
  });
}

export function useEventLog(id: string | null) {
  return useQuery({
    queryKey: id ? queryKeys.eventLog(id) : ["event-logs", "noop"],
    queryFn: () => api<EventLogDetail>(`/api/v1/event-logs/${id}`),
    enabled: !!id,
    refetchInterval: (q) => {
      const data = q.state.data as EventLogDetail | undefined;
      if (!data) return false;
      // Poll through both transient states: `importing` (parsing) and
      // `processing` (modules precomputing) both resolve to `ready`/`failed`.
      return data.status === "importing" || data.status === "processing" ? 1000 : false;
    },
  });
}

// ── Object-centric (OCEL) hooks ──────────────────────────────────────────────

function ocelListPath(logId: string, kind: string, params: OcelListParams): string {
  const qs = new URLSearchParams();
  if (params.offset !== undefined) qs.set("offset", String(params.offset));
  if (params.limit !== undefined) qs.set("limit", String(params.limit));
  if (params.object_type) qs.set("object_type", params.object_type);
  if (params.activity) qs.set("activity", params.activity);
  if (params.q) qs.set("q", params.q);
  return `/api/v1/event-logs/${logId}/ocel/${kind}${qs.toString() ? `?${qs}` : ""}`;
}

export function useOcelOverview(logId: string | null, enabled = true) {
  return useQuery({
    queryKey: logId ? queryKeys.ocelOverview(logId) : ["event-logs", "noop"],
    queryFn: () => api<OcelOverview>(`/api/v1/event-logs/${logId}/ocel/overview`),
    enabled: !!logId && enabled,
  });
}

export function useOcelObjectTypes(logId: string | null, enabled = true) {
  return useQuery({
    queryKey: logId ? queryKeys.ocelObjectTypes(logId) : ["event-logs", "noop"],
    queryFn: () => api<OcelObjectTypeEntry[]>(`/api/v1/event-logs/${logId}/ocel/object-types`),
    enabled: !!logId && enabled,
  });
}

export function useOcelObjects(logId: string, params: OcelListParams = {}) {
  return useQuery({
    queryKey: queryKeys.ocelObjects(logId, params),
    queryFn: () => api<OcelObjectsPage>(ocelListPath(logId, "objects", params)),
  });
}

export function useOcelEvents(logId: string, params: OcelListParams = {}) {
  return useQuery({
    queryKey: queryKeys.ocelEvents(logId, params),
    queryFn: () => api<OcelEventsPage>(ocelListPath(logId, "events", params)),
  });
}

export function useOcelRelationships(logId: string, params: OcelListParams = {}) {
  return useQuery({
    queryKey: queryKeys.ocelRelationships(logId, params),
    queryFn: () => api<OcelRelationsPage>(ocelListPath(logId, "relationships", params)),
  });
}

export function useModules(logId?: string | null) {
  return useQuery({
    queryKey: queryKeys.modules(logId),
    queryFn: () => api<ModuleSummary[]>(modulesListPath(logId)),
  });
}

export function useImportEventLog() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      file: File;
      name?: string;
      csvMapping?: unknown;
      xmlMapping?: unknown;
      jsonMapping?: unknown;
      folderId?: string | null;
    }) => {
      const fd = new FormData();
      fd.append("file", input.file);
      if (input.name) fd.append("name", input.name);
      if (input.csvMapping) fd.append("csv_mapping", JSON.stringify(input.csvMapping));
      if (input.xmlMapping) fd.append("xml_mapping", JSON.stringify(input.xmlMapping));
      if (input.jsonMapping) fd.append("json_mapping", JSON.stringify(input.jsonMapping));
      if (input.folderId) fd.append("folder_id", input.folderId);
      return api<EventLogCreateResponse>("/api/v1/event-logs", { method: "POST", body: fd });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.eventLogs() });
    },
  });
}

export function useImportEventLogFromUrl() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      url: string;
      name?: string;
      csvMapping?: unknown;
      xmlMapping?: unknown;
      jsonMapping?: unknown;
    }) =>
      api<EventLogCreateResponse>("/api/v1/event-logs/from-url", {
        method: "POST",
        json: {
          url: input.url,
          name: input.name || undefined,
          csv_mapping: input.csvMapping ? JSON.stringify(input.csvMapping) : undefined,
          xml_mapping: input.xmlMapping ? JSON.stringify(input.xmlMapping) : undefined,
          json_mapping: input.jsonMapping ? JSON.stringify(input.jsonMapping) : undefined,
        },
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.eventLogs() });
    },
  });
}

export interface XmlProbeField {
  name: string;
  coverage: number;
  samples: string[];
}

export interface XmlProbeResponse {
  // "ocel": object-centric – auto-routed server-side, skip the wizard.
  format_hint: "generic" | "xes" | "ocel";
  event_element: string | null;
  events_sampled: number;
  fields: XmlProbeField[];
  auto_mapping: {
    event_element: string;
    case_id: string;
    activity: string;
    timestamp: string;
    end_timestamp?: string | null;
    resource?: string | null;
    cost?: string | null;
    timestamp_format?: string | null;
    extra?: Record<string, string>;
  } | null;
}

export function useProbeXml() {
  return useMutation({
    mutationFn: async (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      return api<XmlProbeResponse>("/api/v1/event-logs/probe-xml", {
        method: "POST",
        body: fd,
      });
    },
  });
}

export interface JsonProbeResponse {
  format_hint: "generic" | "ocel";
  event_path: string | null;
  events_sampled: number;
  fields: XmlProbeField[];
  auto_mapping: {
    event_path?: string | null;
    case_id: string;
    activity: string;
    timestamp: string;
    end_timestamp?: string | null;
    resource?: string | null;
    cost?: string | null;
    timestamp_format?: string | null;
    extra?: Record<string, string>;
  } | null;
}

export function useProbeJson() {
  return useMutation({
    mutationFn: async (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      return api<JsonProbeResponse>("/api/v1/event-logs/probe-json", {
        method: "POST",
        body: fd,
      });
    },
  });
}

export function useDeleteEventLog() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api<void>(`/api/v1/event-logs/${id}`, { method: "DELETE" }),
    // Optimistic: drop the row from every list cache immediately, roll back on
    // error, reconcile with the server on settle.
    onMutate: async (id) => {
      await qc.cancelQueries({ predicate: (q) => isEventLogListKey(q.queryKey) });
      const prev = qc.getQueriesData<EventLogSummary[]>({
        predicate: (q) => isEventLogListKey(q.queryKey),
      });
      qc.setQueriesData<EventLogSummary[]>(
        { predicate: (q) => isEventLogListKey(q.queryKey) },
        (old) => (Array.isArray(old) ? old.filter((l) => l.id !== id) : old),
      );
      return { prev };
    },
    onError: (_e, _id, ctx) => {
      ctx?.prev?.forEach(([key, data]) => qc.setQueryData(key, data));
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: queryKeys.eventLogs() });
    },
  });
}

export function useDuplicateEventLog() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api<EventLogDetail>(`/api/v1/event-logs/${id}/duplicate`, { method: "POST" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.eventLogs() });
    },
  });
}

// ── Folders ──────────────────────────────────────────────────────────────────

export function useFolders() {
  return useQuery({
    queryKey: queryKeys.folders(),
    queryFn: () => api<FolderSummary[]>("/api/v1/folders"),
  });
}

export function useCreateFolder() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { name: string; parent_id?: string | null }) =>
      api<FolderSummary>("/api/v1/folders", {
        method: "POST",
        json: { name: input.name, parent_id: input.parent_id ?? null },
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.folders() });
    },
  });
}

export function useRenameFolder() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { id: string; name: string }) =>
      api<FolderSummary>(`/api/v1/folders/${input.id}`, {
        method: "PATCH",
        json: { name: input.name },
      }),
    onMutate: async ({ id, name }) => {
      await qc.cancelQueries({ queryKey: queryKeys.folders() });
      const prev = qc.getQueryData<FolderSummary[]>(queryKeys.folders());
      qc.setQueryData<FolderSummary[]>(queryKeys.folders(), (old) =>
        old?.map((f) => (f.id === id ? { ...f, name } : f)),
      );
      return { prev };
    },
    onError: (_e, _vars, ctx) => {
      if (ctx) qc.setQueryData(queryKeys.folders(), ctx.prev);
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: queryKeys.folders() });
    },
  });
}

export function useDeleteFolder() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api<void>(`/api/v1/folders/${id}`, { method: "DELETE" }),
    onMutate: async (id) => {
      await qc.cancelQueries({ queryKey: queryKeys.folders() });
      const prev = qc.getQueryData<FolderSummary[]>(queryKeys.folders());
      qc.setQueryData<FolderSummary[]>(queryKeys.folders(), (old) =>
        old?.filter((f) => f.id !== id),
      );
      return { prev };
    },
    onError: (_e, _id, ctx) => {
      if (ctx) qc.setQueryData(queryKeys.folders(), ctx.prev);
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: queryKeys.folders() });
      // Folder delete promotes children to root, so log placement changes.
      qc.invalidateQueries({ queryKey: queryKeys.eventLogs() });
    },
  });
}

export function useReorderTree() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (items: ReorderItem[]) =>
      api<void>("/api/v1/folders/reorder", {
        method: "POST",
        json: { items },
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.folders() });
      qc.invalidateQueries({ queryKey: queryKeys.eventLogs() });
    },
  });
}

export function useRenameEventLog() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { id: string; name: string }) =>
      api<EventLogDetail>(`/api/v1/event-logs/${input.id}`, {
        method: "PATCH",
        json: { name: input.name },
      }),
    // Optimistic: reflect the new name in every list + the detail right away.
    onMutate: async ({ id, name }) => {
      await qc.cancelQueries({ predicate: (q) => isEventLogListKey(q.queryKey) });
      await qc.cancelQueries({ queryKey: queryKeys.eventLog(id) });
      const prevLists = qc.getQueriesData<EventLogSummary[]>({
        predicate: (q) => isEventLogListKey(q.queryKey),
      });
      const prevDetail = qc.getQueryData<EventLogDetail>(queryKeys.eventLog(id));
      qc.setQueriesData<EventLogSummary[]>(
        { predicate: (q) => isEventLogListKey(q.queryKey) },
        (old) =>
          Array.isArray(old) ? old.map((l) => (l.id === id ? { ...l, name } : l)) : old,
      );
      if (prevDetail) {
        qc.setQueryData<EventLogDetail>(queryKeys.eventLog(id), { ...prevDetail, name });
      }
      return { prevLists, prevDetail, id };
    },
    onError: (_e, _vars, ctx) => {
      ctx?.prevLists?.forEach(([key, data]) => qc.setQueryData(key, data));
      if (ctx) qc.setQueryData(queryKeys.eventLog(ctx.id), ctx.prevDetail);
    },
    onSettled: (_data, _err, vars) => {
      qc.invalidateQueries({ queryKey: queryKeys.eventLogs() });
      qc.invalidateQueries({ queryKey: queryKeys.eventLog(vars.id) });
    },
  });
}

export function useUpdateEventLog(logId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: EventLogUpdatePayload) =>
      api<EventLogDetail>(`/api/v1/event-logs/${logId}`, {
        method: "PATCH",
        json: payload,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.eventLogs() });
      qc.invalidateQueries({ queryKey: queryKeys.eventLog(logId) });
      // Column-override changes affect every events page; clear them.
      qc.invalidateQueries({ queryKey: ["event-logs", logId, "events"] });
    },
  });
}

export function useReimportEventLog() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api<EventLogCreateResponse>(`/api/v1/event-logs/${id}/reimport`, {
        method: "POST",
      }),
    onSuccess: (_data, id) => {
      qc.invalidateQueries({ queryKey: queryKeys.eventLogs() });
      qc.invalidateQueries({ queryKey: queryKeys.eventLog(id) });
    },
  });
}

/** Re-import a log with a user-chosen column-role mapping (settings → Column roles). */
export function useRemapEventLog(logId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (roles: RemapColumnRoles) =>
      api<EventLogCreateResponse>(`/api/v1/event-logs/${logId}/remap`, {
        method: "POST",
        json: roles,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.eventLogs() });
      qc.invalidateQueries({ queryKey: queryKeys.eventLog(logId) });
    },
  });
}

export function useJobsList(params: { status?: string; type?: string; limit?: number } = {}) {
  const qs = new URLSearchParams();
  if (params.status) qs.set("status", params.status);
  if (params.type) qs.set("type", params.type);
  if (params.limit) qs.set("limit", String(params.limit));
  return useQuery({
    queryKey: queryKeys.jobs(Object.fromEntries(qs)),
    queryFn: () => api<JobDetail[]>(`/api/v1/jobs${qs.toString() ? `?${qs}` : ""}`),
  });
}

export function useJob(id: string | null) {
  return useQuery({
    queryKey: id ? queryKeys.job(id) : ["jobs", "noop"],
    queryFn: () => api<JobDetail>(`/api/v1/jobs/${id}`),
    enabled: !!id,
  });
}

export function useCancelJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api<void>(`/api/v1/jobs/${id}/cancel`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useCancelAllJobs() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api<{ cancelled: number }>(`/api/v1/jobs/cancel-all`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useRetryJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api<{ job_id: string }>(`/api/v1/jobs/${id}/retry`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useModuleConfig(moduleId: string) {
  return useQuery({
    queryKey: queryKeys.moduleConfig(moduleId),
    queryFn: () =>
      api<{
        config: Record<string, unknown>;
        enabled: boolean;
        /** When true, an admin has locked this module's config for all users. */
        controlled_by_admin?: boolean;
      }>(`/api/v1/modules/${moduleId}/config`),
  });
}

export function useUninstallModule() {
  const qc = useQueryClient();
  const isModuleListKey = (queryKey: readonly unknown[]) =>
    queryKey[0] === "modules" && queryKey.length === 2;
  return useMutation({
    mutationFn: (id: string) => api(`/api/v1/modules/${id}`, { method: "DELETE" }),
    // Optimistic: pull the card from every module list cache immediately.
    onMutate: async (id) => {
      await qc.cancelQueries({ predicate: (q) => isModuleListKey(q.queryKey) });
      const prev = qc.getQueriesData<ModuleSummary[]>({
        predicate: (q) => isModuleListKey(q.queryKey),
      });
      qc.setQueriesData<ModuleSummary[]>(
        { predicate: (q) => isModuleListKey(q.queryKey) },
        (old) => (Array.isArray(old) ? old.filter((m) => m.id !== id) : old),
      );
      return { prev };
    },
    onError: (_e, _id, ctx) => {
      ctx?.prev?.forEach(([key, data]) => qc.setQueryData(key, data));
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ["modules"] }),
  });
}

export function useRestoreDefaults() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api<{ restored: string[] }>("/api/v1/modules/restore-defaults", {
        method: "POST",
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["modules"] }),
  });
}

export interface AiModelSlot {
  title: string;
  description?: string | null;
}

export interface AiModelsManifest {
  /** When true the module owns its own OpenAI key (isolated from Settings → AI). */
  self_hosted?: boolean;
  llm?: AiModelSlot | null;
  embedding?: AiModelSlot | null;
  [extra: string]: AiModelSlot | boolean | null | undefined;
}

export interface ModelStoreManifest {
  title?: string;
  description?: string | null;
  /** Accepted upload extension(s) for the file picker, e.g. ".tar.zst". */
  accept?: string;
  /** Config key under which the selected model's folder name is persisted. */
  config_key?: string;
}

export interface ModuleManifest {
  config_schema?: Record<string, unknown> | null;
  ai_models?: AiModelsManifest | null;
  model_store?: ModelStoreManifest | null;
  [extra: string]: unknown;
}

export function useModuleManifest(moduleId: string) {
  return useQuery({
    queryKey: queryKeys.moduleManifest(moduleId),
    queryFn: () => api<ModuleManifest>(`/api/v1/modules/${moduleId}/manifest`),
    staleTime: Infinity,
  });
}

export function useUpdateModuleConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { id: string; config: Record<string, unknown>; enabled: boolean }) =>
      api(`/api/v1/modules/${input.id}/config`, {
        method: "PUT",
        json: { config: input.config, enabled: input.enabled },
      }),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: queryKeys.moduleConfig(vars.id) });
    },
  });
}

export interface ModuleModel {
  name: string;
  size_bytes: number;
  /** The account's explicit selection (config), may differ from `active`. */
  selected: boolean;
  /** What detection would actually load right now. */
  active: boolean;
}

export interface ModuleModelsResponse {
  models: ModuleModel[];
  selected: string | null;
  active: string | null;
  /** Admin pinned one shared model platform-wide; the per-user picker is
   *  then read-only (Admin → Controls → CV4CDD detection model). */
  locked?: boolean;
}

/** List the platform-wide models installed for a module (e.g. cv4cdd). */
export function useModuleModels(moduleId: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.moduleModels(moduleId),
    queryFn: () => api<ModuleModelsResponse>(`/api/v1/modules/${moduleId}/models`),
    enabled,
  });
}

/** Upload a model archive (multipart). Shared across the whole platform. */
export function useUploadModuleModel(moduleId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => {
      const body = new FormData();
      body.append("file", file);
      return api<ModuleModel>(`/api/v1/modules/${moduleId}/models`, {
        method: "POST",
        body,
      });
    },
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: queryKeys.moduleModels(moduleId) }),
  });
}

/** Delete an installed model – removes it for every account on the platform. */
export function useDeleteModuleModel(moduleId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) =>
      api<{ deleted: string }>(
        `/api/v1/modules/${moduleId}/models/${encodeURIComponent(name)}`,
        { method: "DELETE" },
      ),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: queryKeys.moduleModels(moduleId) }),
  });
}

export interface RecreateIndexResponse {
  ok: boolean;
  index_name: string;
  dimension: number;
}

export function useRecreateModuleIndex(moduleId: string) {
  return useMutation({
    mutationFn: () =>
      api<RecreateIndexResponse>(
        `/api/v1/modules/${moduleId}/pinecone/recreate-index`,
        { method: "POST" },
      ),
  });
}

export interface ModuleAiCheckResponse {
  ok: boolean;
  models: string[];
}

/** Validate a self-hosted module's OpenAI key and list its available models.
 *  Pass the unsaved draft key to check before saving, or `null` to use the
 *  key already persisted in the module config. */
export function useModuleAiCheck(moduleId: string) {
  return useMutation({
    mutationFn: (apiKey: string | null) =>
      api<ModuleAiCheckResponse>(`/api/v1/modules/${moduleId}/ai/check`, {
        method: "POST",
        json: { api_key: apiKey },
      }),
  });
}

// ── Events / Variants / Quality / Edits ─────────────────────────────────────

export function useEventLogRows(logId: string, params: EventsListParams) {
  return useQuery({
    queryKey: queryKeys.events(logId, params),
    queryFn: () => api<EventsPage>(eventsPath(logId, params)),
    enabled: !!logId,
    placeholderData: (prev) => prev,
    staleTime: 5_000,
  });
}

export function usePatchEventRow(logId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { rowIndex: number; patch: CellPatch }) =>
      api<CellPatchResult>(`/api/v1/event-logs/${logId}/events/${input.rowIndex}`, {
        method: "PATCH",
        json: input.patch,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["event-logs", logId, "events"] });
      qc.invalidateQueries({ queryKey: queryKeys.eventLog(logId) });
      qc.invalidateQueries({ queryKey: queryKeys.eventLogs() });
      // Variants and data-quality both depend on the parquet too.
      qc.invalidateQueries({ queryKey: ["event-logs", logId, "variants"] });
      qc.invalidateQueries({ queryKey: queryKeys.dataQuality(logId) });
      qc.invalidateQueries({ queryKey: ["event-logs", logId, "edits"] });
    },
  });
}

export function useBulkFillEventRows(logId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: BulkFillBody) =>
      api<BulkFillResult>(`/api/v1/event-logs/${logId}/events/bulk-fill`, {
        method: "POST",
        json: body,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["event-logs", logId, "events"] });
      qc.invalidateQueries({ queryKey: queryKeys.eventLog(logId) });
      qc.invalidateQueries({ queryKey: ["event-logs", logId, "variants"] });
      qc.invalidateQueries({ queryKey: queryKeys.dataQuality(logId) });
      qc.invalidateQueries({ queryKey: ["event-logs", logId, "edits"] });
    },
  });
}

/** Distinct values + counts for one column – backs the filter checklist. */
export function useColumnValues(
  logId: string,
  field: string,
  q: string,
  enabled = true,
) {
  const qs = new URLSearchParams();
  if (q) qs.set("q", q);
  const path = `/api/v1/event-logs/${logId}/columns/${encodeURIComponent(
    field,
  )}/values${qs.toString() ? `?${qs}` : ""}`;
  return useQuery({
    queryKey: queryKeys.columnValues(logId, field, q),
    queryFn: () => api<ColumnValuesPage>(path),
    enabled: enabled && !!logId && !!field,
    placeholderData: (prev) => prev,
    staleTime: 30_000,
  });
}

/** Commit the Events-tab filter as the applied dataset filter, re-running all
 * modules. Pass `[]` to clear it (Restore). */
export function useApplyActiveFilter(logId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (filter: FilterEntry[]) =>
      api<ActiveFilterResult>(`/api/v1/event-logs/${logId}/active-filter`, {
        method: "PUT",
        json: { filter },
      }),
    onSuccess: () => {
      // The detail carries active_filter; reseed the editor + downstream tabs.
      qc.invalidateQueries({ queryKey: queryKeys.eventLog(logId) });
      qc.invalidateQueries({ queryKey: ["event-logs", logId, "variants"] });
      qc.invalidateQueries({ queryKey: queryKeys.dataQuality(logId) });
      qc.invalidateQueries({ queryKey: queryKeys.activities(logId) });
      // Modules reprocess the new dataset; refresh their surfaces + the dock.
      qc.invalidateQueries({ queryKey: ["modules"] });
      qc.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}

export function useVariants(logId: string, params: VariantsListParams) {
  return useQuery({
    queryKey: queryKeys.variants(logId, params),
    queryFn: () => api<VariantsPage>(variantsPath(logId, params)),
    enabled: !!logId,
    placeholderData: (prev) => prev,
    staleTime: 30_000,
  });
}

export function useVariant(logId: string, variantId: string | null) {
  return useQuery({
    queryKey: variantId
      ? queryKeys.variant(logId, variantId)
      : ["event-logs", logId, "variants", "noop"],
    queryFn: () =>
      api<VariantDetail>(`/api/v1/event-logs/${logId}/variants/${variantId}`),
    enabled: !!logId && !!variantId,
    staleTime: 30_000,
  });
}

export function useVariantCases(
  logId: string,
  variantId: string | null,
  offset = 0,
  limit = 100,
) {
  return useQuery({
    queryKey: variantId
      ? queryKeys.variantCases(logId, variantId, offset, limit)
      : ["event-logs", logId, "variants", "noop", "cases"],
    queryFn: () =>
      api<VariantCasesPage>(
        `/api/v1/event-logs/${logId}/variants/${variantId}/cases?offset=${offset}&limit=${limit}`,
      ),
    enabled: !!logId && !!variantId,
    staleTime: 30_000,
  });
}

export function useDataQuality(logId: string) {
  return useQuery({
    queryKey: queryKeys.dataQuality(logId),
    queryFn: () => api<DataQuality>(`/api/v1/event-logs/${logId}/data-quality`),
    enabled: !!logId,
    staleTime: 30_000,
  });
}

export function useActivities(logId: string) {
  return useQuery({
    queryKey: queryKeys.activities(logId),
    queryFn: () => api<ActivitiesPage>(`/api/v1/event-logs/${logId}/activities`),
    enabled: !!logId,
    staleTime: 30_000,
  });
}

export function useEventEdits(logId: string, offset = 0, limit = 50) {
  return useQuery({
    queryKey: queryKeys.edits(logId, offset, limit),
    queryFn: () =>
      api<EventEditsPage>(
        `/api/v1/event-logs/${logId}/edits?offset=${offset}&limit=${limit}`,
      ),
    enabled: !!logId,
    staleTime: 10_000,
  });
}
