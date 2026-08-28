"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api, rawFetch } from "@/lib/api";
import type {
  BpmnData,
  DfgData,
  DfgLayoutRequest,
  DfgLayoutResponse,
  PetriNetData,
  PrefixTreeData,
  ProcessTreeData,
} from "./types";

const STALE_TIME = 30_000;

function discoveryUrl(path: string, logId: string, params: Record<string, string | number | undefined> = {}): string {
  const search = new URLSearchParams({ log_id: logId });
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined) search.set(k, String(v));
  }
  return `/api/v1/modules/discovery${path}?${search.toString()}`;
}

export function useDiscoveryDfg(logId: string, variantPct?: number) {
  return useQuery<DfgData>({
    queryKey: ["modules", "discovery", "dfg", logId, variantPct ?? 1],
    queryFn: () =>
      api<DfgData>(
        discoveryUrl("/dfg", logId, variantPct !== undefined && variantPct < 1 ? { variant_pct: variantPct } : {}),
      ),
    enabled: Boolean(logId),
    staleTime: STALE_TIME,
  });
}

export function useDiscoveryPetriAlpha(logId: string) {
  return useQuery<PetriNetData>({
    queryKey: ["modules", "discovery", "petri-alpha", logId],
    queryFn: () => api<PetriNetData>(discoveryUrl("/petri-net/alpha", logId)),
    enabled: Boolean(logId),
    staleTime: STALE_TIME,
  });
}

export function useDiscoveryPetriInductive(logId: string) {
  return useQuery<PetriNetData>({
    queryKey: ["modules", "discovery", "petri-inductive", logId],
    queryFn: () => api<PetriNetData>(discoveryUrl("/petri-net/inductive", logId)),
    enabled: Boolean(logId),
    staleTime: STALE_TIME,
  });
}

export function useDiscoveryProcessTree(logId: string) {
  return useQuery<ProcessTreeData>({
    queryKey: ["modules", "discovery", "process-tree", logId],
    queryFn: () => api<ProcessTreeData>(discoveryUrl("/process-tree", logId)),
    enabled: Boolean(logId),
    staleTime: STALE_TIME,
  });
}

export function useDiscoveryPetriAlphaPlus(logId: string) {
  return useQuery<PetriNetData>({
    queryKey: ["modules", "discovery", "petri-alpha-plus", logId],
    queryFn: () => api<PetriNetData>(discoveryUrl("/petri-net/alpha-plus", logId)),
    enabled: Boolean(logId),
    staleTime: STALE_TIME,
  });
}

// ILP is opt-in: it never runs on mount alongside the other miners. The LP
// solve per causal relation is heavy (and has OOM-crashed when run eagerly),
// so the caller passes `enabled` only once the user explicitly selects it.
export function useDiscoveryPetriIlp(logId: string, enabled: boolean) {
  return useQuery<PetriNetData>({
    queryKey: ["modules", "discovery", "petri-ilp", logId],
    queryFn: () => api<PetriNetData>(discoveryUrl("/petri-net/ilp", logId)),
    enabled: Boolean(logId) && enabled,
    staleTime: STALE_TIME,
  });
}

export function useDiscoveryPetriImf(logId: string, noiseThreshold: number) {
  return useQuery<PetriNetData>({
    queryKey: ["modules", "discovery", "petri-imf", logId, noiseThreshold],
    queryFn: () =>
      api<PetriNetData>(discoveryUrl("/petri-net/imf", logId, { noise_threshold: noiseThreshold })),
    enabled: Boolean(logId),
    staleTime: STALE_TIME,
  });
}

export function useDiscoveryProcessTreeImf(logId: string, noiseThreshold: number) {
  return useQuery<ProcessTreeData>({
    queryKey: ["modules", "discovery", "process-tree-imf", logId, noiseThreshold],
    queryFn: () =>
      api<ProcessTreeData>(discoveryUrl("/process-tree/imf", logId, { noise_threshold: noiseThreshold })),
    enabled: Boolean(logId),
    staleTime: STALE_TIME,
  });
}

export function useDiscoveryPrefixTree(logId: string) {
  return useQuery<PrefixTreeData>({
    queryKey: ["modules", "discovery", "prefix-tree", logId],
    queryFn: () => api<PrefixTreeData>(discoveryUrl("/prefix-tree", logId)),
    enabled: Boolean(logId),
    staleTime: STALE_TIME,
  });
}

export type BpmnAlgo = "inductive" | "imf";

function bpmnQueryKey(logId: string, algo: BpmnAlgo, noiseThreshold: number) {
  return ["modules", "discovery", "bpmn", logId, algo, noiseThreshold] as const;
}

export function useDiscoveryBpmn(
  logId: string,
  algo: BpmnAlgo = "inductive",
  noiseThreshold = 0.2,
) {
  return useQuery<BpmnData>({
    queryKey: bpmnQueryKey(logId, algo, noiseThreshold),
    queryFn: () =>
      api<BpmnData>(
        discoveryUrl(
          "/bpmn",
          logId,
          algo === "imf" ? { algo, noise_threshold: noiseThreshold } : { algo },
        ),
      ),
    enabled: Boolean(logId),
    staleTime: STALE_TIME,
  });
}

/**
 * Fetch the active BPMN with auth and save it client-side as `process.bpmn`.
 *
 * A plain `<a href={apiUrl(...)} download>` navigates the browser straight to
 * the API, which omits the Keycloak bearer token (only the `@/lib/api` fetch
 * wrappers attach it) and the endpoint replies 401 "Missing or invalid bearer
 * token". Going through `rawFetch` attaches the token, then we trigger the
 * download from the in-memory blob.
 */
export async function downloadBpmn(logId: string): Promise<void> {
  const res = await rawFetch(discoveryUrl("/bpmn/download", logId));
  if (!res.ok) throw new Error(`Download failed (${res.status})`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "process.bpmn";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/** FNV-1a over the request graph — keeps the query key short and stable. */
function layoutRequestKey(request: DfgLayoutRequest): string {
  const canonical = JSON.stringify({
    n: request.nodes.map((n) => `${n.id}:${n.width}x${n.height}`).sort(),
    e: request.edges.map(([s, t]) => `${s}>${t}`).sort(),
    s: request.start_id,
    t: request.end_id,
    p: request.params ?? {},
  });
  let hash = 0x811c9dc5;
  for (let i = 0; i < canonical.length; i++) {
    hash ^= canonical.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(16);
}

/**
 * Server-side layout for the visible DFG subgraph (POST /dfg/layout).
 *
 * `keepPreviousData`: a slider tweak refetches with new nodes — the previous
 * geometry stays rendered until the fresh one lands, so the canvas morphs
 * instead of flashing a skeleton. The server caches by graph digest, so
 * repeat requests are near-instant.
 */
export function useDfgServerLayout(logId: string, request: DfgLayoutRequest | null) {
  return useQuery<DfgLayoutResponse>({
    queryKey: [
      "modules",
      "discovery",
      "dfg-layout",
      logId,
      request?.algorithm ?? "",
      request ? layoutRequestKey(request) : "",
    ],
    queryFn: () =>
      api<DfgLayoutResponse>(discoveryUrl("/dfg/layout", logId), {
        method: "POST",
        json: request,
      }),
    enabled: Boolean(logId) && request !== null && request.nodes.length > 0,
    staleTime: STALE_TIME,
    placeholderData: keepPreviousData,
    retry: 1,
  });
}

export interface HeuristicsThresholds {
  dependency_threshold?: number;
  and_threshold?: number;
  loop_two_threshold?: number;
}

export function useDiscoveryHeuristicsNet(logId: string, thresholds: HeuristicsThresholds = {}) {
  return useQuery<DfgData>({
    queryKey: ["modules", "discovery", "heuristics-net", logId, thresholds],
    queryFn: () =>
      api<DfgData>(
        discoveryUrl("/heuristics-net", logId, {
          dependency_threshold: thresholds.dependency_threshold,
          and_threshold: thresholds.and_threshold,
          loop_two_threshold: thresholds.loop_two_threshold,
        }),
      ),
    enabled: Boolean(logId),
    staleTime: STALE_TIME,
  });
}
