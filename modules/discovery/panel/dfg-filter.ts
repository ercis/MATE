/**
 * Celonis-style DFG filtering – pure utility shared by `DfgCanvas` (rendering)
 * and `DfgTab` (slider count labels) so the two never disagree.
 *
 * Semantics:
 *
 *   1. Activities are sorted by frequency desc; the slider keeps the top-N.
 *   2. Edges that touch a hidden activity are dropped (the canvas can't show
 *      a dangling edge).
 *   3. The remaining "candidate" edges are the universe the connections
 *      slider operates on. Top-N by frequency.
 *   4. **Connectivity floor**: even at slider = 0, we still include enough
 *      edges to keep every visible activity reachable. This is a minimum
 *      spanning forest over the candidate edges (Kruskal – sort by frequency
 *      desc, greedy union-find), so the chosen "must-keep" edges are the
 *      most-frequent ones.
 *
 *   Visible edges = union(spanning_set, top_N_by_user_slider).
 */

import type { DfgActivity, DfgData, DfgEdge } from "./types";
import type { DfgRenderSettings } from "@/lib/stores/visualization-settings";

export interface DfgFilterResult {
  visibleActivities: DfgActivity[];
  visibleActivityIds: Set<string>;
  /** Candidate edges (after activity filter + self-loop toggle). */
  candidateEdges: DfgEdge[];
  /** Edges that ended up rendered (top-N ∪ spanning, then optional 95% cut). */
  visibleEdges: DfgEdge[];
  /** Edges in the spanning forest – the "floor" the connections slider can't go below. */
  spanningEdgeIds: Set<string>;
}

export function computeDfgVisibility(
  data: DfgData,
  settings: DfgRenderSettings,
): DfgFilterResult {
  // 1. Top-N activities by frequency.
  const sortedActivities = [...data.activities].sort((a, b) => b.frequency - a.frequency);
  const activityCount = sortedActivities.length === 0
    ? 0
    : Math.max(
        1,
        Math.min(sortedActivities.length, Math.ceil(sortedActivities.length * settings.activitiesShown)),
      );
  const visibleActivities = sortedActivities.slice(0, activityCount);
  const visibleActivityIds = new Set(visibleActivities.map((a) => a.id));

  // 2/3. Candidate edges: between visible activities, optionally drop self-loops.
  const candidateEdges = data.edges
    .filter((e) => visibleActivityIds.has(e.source) && visibleActivityIds.has(e.target))
    .filter((e) => !(settings.hideSelfLoops && e.source === e.target));

  const sortedEdges = [...candidateEdges].sort((a, b) => b.frequency - a.frequency);

  // 4. Spanning forest by Kruskal – greedy over frequency-sorted edges.
  const parent = new Map<string, string>();
  for (const id of visibleActivityIds) parent.set(id, id);

  const find = (x: string): string => {
    const p = parent.get(x);
    if (p === undefined || p === x) return x;
    const root = find(p);
    parent.set(x, root);
    return root;
  };
  const union = (a: string, b: string): boolean => {
    const ra = find(a);
    const rb = find(b);
    if (ra === rb) return false;
    parent.set(ra, rb);
    return true;
  };

  const spanningEdgeIds = new Set<string>();
  for (const e of sortedEdges) {
    if (e.source === e.target) continue; // self-loops can't bridge components
    if (union(e.source, e.target)) spanningEdgeIds.add(e.id);
  }

  // User's top-N.
  const userN = Math.max(
    0,
    Math.min(sortedEdges.length, Math.ceil(sortedEdges.length * settings.connectionsShown)),
  );
  const userTopIds = new Set(sortedEdges.slice(0, userN).map((e) => e.id));

  // Visible = union (preserve sorted order).
  let visibleEdges = sortedEdges.filter(
    (e) => userTopIds.has(e.id) || spanningEdgeIds.has(e.id),
  );

  // "Top N% edges" hard-cut: remove the bottom (100-N)% by count without
  // touching node visibility. Unlike the spanning floor this CAN drop any edge.
  const edgeTopPercent = settings.edgeTopPercent ?? 100;
  if (edgeTopPercent < 100 && visibleEdges.length > 1) {
    const removeCount = Math.floor(visibleEdges.length * (1 - edgeTopPercent / 100));
    if (removeCount > 0) {
      const byFreqAsc = [...visibleEdges].sort((a, b) => a.frequency - b.frequency);
      const removeIds = new Set(byFreqAsc.slice(0, removeCount).map((e) => e.id));
      visibleEdges = visibleEdges.filter((e) => !removeIds.has(e.id));
    }
  }

  return {
    visibleActivities,
    visibleActivityIds,
    candidateEdges,
    visibleEdges,
    spanningEdgeIds,
  };
}
