/**
 * Pure, server-safe query keys + prefetch path builders.
 *
 * This module has NO `"use client"` directive and imports NO `api` / `auth` /
 * `next/headers` — so it can be shared by both the client query hooks
 * (`lib/queries.ts`, `lib/dashboard-queries.ts`) and the server-side prefetch
 * layer (`lib/prefetch.ts`). Importing the keys directly from a `"use client"`
 * module into a Server Component would turn them into client references that
 * throw when called on the server, hence this split.
 *
 * Keys live here so the SSR prefetch and the client hook share ONE source of
 * truth — a mismatch would silently break hydration (the client would refetch).
 */

import type { FilterEntry } from "@/lib/api-types";

export interface OcelListParams {
  offset?: number;
  limit?: number;
  object_type?: string;
  activity?: string;
  q?: string;
}

export interface EventsListParams {
  offset?: number;
  limit?: number;
  sort?: string;
  filter?: FilterEntry[];
  q?: string;
  missing_only?: boolean;
  case_id?: string;
}

export interface VariantsListParams {
  offset?: number;
  limit?: number;
  sort?: string;
  activity_contains?: string;
  min_case_count?: number;
}

export const queryKeys = {
  folders: () => ["folders"] as const,
  eventLogs: () => ["event-logs"] as const,
  eventLog: (id: string) => ["event-logs", id] as const,
  events: (logId: string, params: EventsListParams) =>
    ["event-logs", logId, "events", params] as const,
  variants: (logId: string, params: VariantsListParams) =>
    ["event-logs", logId, "variants", params] as const,
  variant: (logId: string, variantId: string) =>
    ["event-logs", logId, "variants", variantId] as const,
  variantCases: (logId: string, variantId: string, offset: number, limit: number) =>
    ["event-logs", logId, "variants", variantId, "cases", offset, limit] as const,
  dataQuality: (logId: string) => ["event-logs", logId, "data-quality"] as const,
  activities: (logId: string) => ["event-logs", logId, "activities"] as const,
  ocelOverview: (logId: string) => ["event-logs", logId, "ocel", "overview"] as const,
  ocelObjectTypes: (logId: string) => ["event-logs", logId, "ocel", "object-types"] as const,
  ocelObjects: (logId: string, params: OcelListParams) =>
    ["event-logs", logId, "ocel", "objects", params] as const,
  ocelEvents: (logId: string, params: OcelListParams) =>
    ["event-logs", logId, "ocel", "events", params] as const,
  ocelRelationships: (logId: string, params: OcelListParams) =>
    ["event-logs", logId, "ocel", "relationships", params] as const,
  columnValues: (logId: string, field: string, q: string) =>
    ["event-logs", logId, "column-values", field, q] as const,
  edits: (logId: string, offset: number, limit: number) =>
    ["event-logs", logId, "edits", offset, limit] as const,
  modules: (logId?: string | null) => ["modules", logId ?? null] as const,
  moduleManifest: (id: string) => ["modules", id, "manifest"] as const,
  moduleConfig: (id: string) => ["modules", id, "config"] as const,
  moduleModels: (id: string) => ["modules", id, "models"] as const,
  jobs: (params?: Record<string, string>) => ["jobs", params ?? {}] as const,
  job: (id: string) => ["jobs", id] as const,
};

export const dashboardKeys = {
  all: () => ["dashboards"] as const,
  detail: (id: string) => ["dashboards", id] as const,
  cards: () => ["dashboard-cards"] as const,
};

// ── Prefetch path builders ───────────────────────────────────────────────────
// One source of truth for the URLs the client hooks fetch, so `lib/prefetch.ts`
// (server, `apiServer`) and the hooks (browser, `api`) build the identical path.

export function eventLogsListPath(params: { q?: string; status?: string } = {}): string {
  const qs = new URLSearchParams();
  if (params.q) qs.set("q", params.q);
  if (params.status) qs.set("status", params.status);
  return `/api/v1/event-logs${qs.toString() ? `?${qs}` : ""}`;
}

export function eventLogPath(id: string): string {
  return `/api/v1/event-logs/${id}`;
}

export function dashboardsListPath(): string {
  return "/api/v1/dashboards";
}

export function dashboardPath(id: string): string {
  return `/api/v1/dashboards/${id}`;
}

export function modulesListPath(logId?: string | null): string {
  const qs = logId ? `?log_id=${encodeURIComponent(logId)}` : "";
  return `/api/v1/modules${qs}`;
}

export function moduleManifestPath(id: string): string {
  return `/api/v1/modules/${id}/manifest`;
}

export function moduleConfigPath(id: string): string {
  return `/api/v1/modules/${id}/config`;
}
