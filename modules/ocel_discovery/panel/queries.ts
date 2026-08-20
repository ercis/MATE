"use client";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";

const STALE_TIME = 30_000;

export interface ObjectTypeCount {
  type: string;
  count: number;
}

export interface OcelSummary {
  object_types: ObjectTypeCount[];
  objects_count: number;
  events_count: number;
  activities_count: number;
}

export interface OcdfgEdge {
  object_type: string;
  source: string;
  target: string;
  /** Alias of `unique_objects`, kept for back-compat. */
  count: number;
  unique_objects: number;
  events: number;
  total_objects: number;
  /** Mean / median edge duration in seconds; null when not computable. */
  perf_mean: number | null;
  perf_median: number | null;
}

export interface OcdfgActivity {
  object_type: string;
  activity: string;
  count: number;
  unique_objects: number;
  events: number;
  total_objects: number;
}

/** Selectable OC-DFG frequency measure (maps to a numeric field on edges/acts). */
export type OcdfgMeasure = "unique_objects" | "events" | "total_objects";

export const OCDFG_MEASURE_LABELS: Record<OcdfgMeasure, string> = {
  unique_objects: "Unique objects",
  events: "Events",
  total_objects: "Total objects",
};

export interface OcdfgData {
  activities: string[];
  object_types: string[];
  edges: OcdfgEdge[];
  start_activities: OcdfgActivity[];
  end_activities: OcdfgActivity[];
}

function url(path: string, logId: string): string {
  return `/api/v1/modules/ocel_discovery${path}?log_id=${encodeURIComponent(logId)}`;
}

export function useOcelSummary(logId: string) {
  return useQuery<OcelSummary>({
    queryKey: ["modules", "ocel_discovery", "summary", logId],
    queryFn: () => api<OcelSummary>(url("/summary", logId)),
    enabled: Boolean(logId),
    staleTime: STALE_TIME,
  });
}

export function useOcdfg(logId: string) {
  return useQuery<OcdfgData>({
    queryKey: ["modules", "ocel_discovery", "ocdfg", logId],
    queryFn: () => api<OcdfgData>(url("/ocdfg", logId)),
    enabled: Boolean(logId),
    staleTime: STALE_TIME,
  });
}

export interface OcpnPlace {
  id: string;
  label: string;
  is_initial: boolean;
  is_final: boolean;
}

export interface OcpnTransition {
  id: string;
  label: string;
  silent: boolean;
}

export interface OcpnArc {
  id: string;
  source: string;
  target: string;
  variable: boolean;
}

export interface OcpnNet {
  object_type: string;
  places: OcpnPlace[];
  transitions: OcpnTransition[];
  arcs: OcpnArc[];
}

export interface OcpnData {
  object_types: string[];
  activities: string[];
  nets: OcpnNet[];
}

export function useOcpn(logId: string) {
  return useQuery<OcpnData>({
    queryKey: ["modules", "ocel_discovery", "ocpn", logId],
    queryFn: () => api<OcpnData>(url("/ocpn", logId)),
    enabled: Boolean(logId),
    staleTime: STALE_TIME,
  });
}

// --------------------------------------------------------------------------
// Object interaction / relation graph
// --------------------------------------------------------------------------

export type ObjectGraphType =
  | "object_interaction"
  | "object_descendants"
  | "object_inheritance"
  | "object_cobirth"
  | "object_codeath";

export const OBJECT_GRAPH_LABELS: Record<ObjectGraphType, string> = {
  object_interaction: "Interaction",
  object_descendants: "Descendants",
  object_inheritance: "Inheritance",
  object_cobirth: "Co-birth",
  object_codeath: "Co-death",
};

export interface ObjectGraphNode {
  type: string;
  count: number;
  /** Within-type object pairs (same-type interactions), folded onto the node. */
  intra_count: number;
}

export interface ObjectGraphEdge {
  source: string;
  target: string;
  count: number;
  directed: boolean;
}

export interface ObjectGraphData {
  graph_type: ObjectGraphType;
  directed: boolean;
  object_types: ObjectGraphNode[];
  edges: ObjectGraphEdge[];
}

export function useObjectsGraph(logId: string, graphType: ObjectGraphType) {
  return useQuery<ObjectGraphData>({
    queryKey: ["modules", "ocel_discovery", "objects-graph", logId, graphType],
    queryFn: () =>
      api<ObjectGraphData>(
        `${url("/objects-graph", logId)}&graph_type=${encodeURIComponent(graphType)}`,
      ),
    enabled: Boolean(logId),
    staleTime: STALE_TIME,
  });
}

// --------------------------------------------------------------------------
// Activity / object-type matrix
// --------------------------------------------------------------------------

export interface ActivityObjectTypeCell {
  activity: string;
  object_type: string;
  events: number;
  objects: number;
}

export interface ActivityObjectTypesData {
  activities: string[];
  object_types: string[];
  cells: ActivityObjectTypeCell[];
}

export function useActivityObjectTypes(logId: string) {
  return useQuery<ActivityObjectTypesData>({
    queryKey: ["modules", "ocel_discovery", "activity-object-types", logId],
    queryFn: () => api<ActivityObjectTypesData>(url("/activity-object-types", logId)),
    enabled: Boolean(logId),
    staleTime: STALE_TIME,
  });
}

// --------------------------------------------------------------------------
// Object lifecycle summary
// --------------------------------------------------------------------------

export interface ObjectTypeLifecycle {
  type: string;
  objects: number;
  median_duration_s: number | null;
  avg_duration_s: number | null;
  avg_events: number | null;
  avg_interacting: number | null;
}

export interface ObjectLifecycleRow {
  oid: string;
  type: string;
  duration_s: number;
  n_events: number;
}

export interface ObjectsSummaryData {
  types: ObjectTypeLifecycle[];
  top_objects: ObjectLifecycleRow[];
  has_interacting: boolean;
}

export function useObjectsSummary(logId: string) {
  return useQuery<ObjectsSummaryData>({
    queryKey: ["modules", "ocel_discovery", "objects-summary", logId],
    queryFn: () => api<ObjectsSummaryData>(url("/objects-summary", logId)),
    enabled: Boolean(logId),
    staleTime: STALE_TIME,
  });
}
