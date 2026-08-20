import { Position, type Edge, type Node } from "@xyflow/react";

/**
 * Happy Path Tower layout: the dominant happy path forms a straight central
 * spine along the primary time axis; all other activities are grouped into
 * the column of the nearest happy-path step (by mean_trace_position) and
 * stacked symmetrically around that step in the perpendicular direction.
 *
 * Algorithm
 * ---------
 * 1. Discover the happy path via a greedy frequency walk from the highest-
 *    frequency start activity, always following the highest-frequency edge.
 * 2. Order happy-path activities by mean_trace_position → defines the column sequence.
 * 3. Assign every non-HP node to the column whose HP activity has the nearest rank.
 * 4. Within each column: HP node at perp = 0; non-HP nodes sorted by proximity
 *    to their column's HP rank, then placed symmetrically (±1, ±2 … slots).
 * 5. Primary axis: uniform spacing regardless of actual temporal distance.
 * 6. Fallback: if rank data is missing, sorts by frequency; empty happy path → single column.
 */

interface HappyPathOpts {
  edgeFrequency: (source: string, target: string) => number;
  frequencyByNode: (nodeId: string) => number;
  startActivityIds: Set<string>;
  endActivityIds: Set<string>;
}

function findHappyPath(
  nodeIds: Set<string>,
  edges: Array<{ source: string; target: string }>,
  opts: HappyPathOpts,
): Set<string> {
  const candidates = opts.startActivityIds.size > 0
    ? [...opts.startActivityIds].filter((id) => nodeIds.has(id))
    : [...nodeIds];
  if (candidates.length === 0) return new Set();

  let seed = candidates[0]!;
  let bestFreq = -1;
  for (const id of candidates) {
    const f = opts.frequencyByNode(id);
    if (f > bestFreq) { bestFreq = f; seed = id; }
  }

  const outEdges = new Map<string, Array<{ target: string }>>();
  for (const id of nodeIds) outEdges.set(id, []);
  for (const e of edges) {
    if (e.source !== e.target && nodeIds.has(e.source) && nodeIds.has(e.target)) {
      outEdges.get(e.source)!.push({ target: e.target });
    }
  }

  const path = new Set<string>([seed]);
  let current = seed;
  for (;;) {
    const outs = outEdges.get(current) ?? [];
    let bestTarget: string | null = null;
    let bestEdgeFreq = -1;
    for (const { target } of outs) {
      if (path.has(target)) continue;
      const f = opts.edgeFrequency(current, target);
      if (f > bestEdgeFreq) { bestEdgeFreq = f; bestTarget = target; }
    }
    if (bestTarget === null) break;
    path.add(bestTarget);
    current = bestTarget;
    if (opts.endActivityIds.has(current)) break;
  }
  return path;
}

export interface HappyPathTowerOptions {
  direction: "LR" | "RL" | "TB" | "BT";
  nodeSize: { width: number; height: number };
  /** Gap between columns as a multiple of nodeSize.height. Default: 4. */
  phaseGapMultiplier?: number;
  /** Gap between stacked nodes within a column as a multiple of nodeSize.height. Default: 1. */
  nodeSpacingMultiplier?: number;
  /** Temporal rank ∈ [0, 1] for each node id (mean_trace_position). */
  rankByNode: (nodeId: string) => number | undefined;
  edgeFrequency: (source: string, target: string) => number;
  frequencyByNode: (nodeId: string) => number;
  startActivityIds: Set<string>;
  endActivityIds: Set<string>;
}

const DIRECTION_HANDLES: Record<
  HappyPathTowerOptions["direction"],
  { source: Position; target: Position }
> = {
  LR: { source: Position.Right, target: Position.Left },
  RL: { source: Position.Left, target: Position.Right },
  TB: { source: Position.Bottom, target: Position.Top },
  BT: { source: Position.Top, target: Position.Bottom },
};

export function happyPathTowerLayout<
  TNodeData extends Record<string, unknown>,
  TEdgeData extends Record<string, unknown>,
>(
  nodes: Node<TNodeData>[],
  edges: Edge<TEdgeData>[],
  opts: HappyPathTowerOptions,
): { nodes: Node<TNodeData>[]; edges: Edge<TEdgeData>[] } {
  if (nodes.length === 0) return { nodes, edges };

  const phaseGapMultiplier = opts.phaseGapMultiplier ?? 4;
  const nodeSpacingMultiplier = opts.nodeSpacingMultiplier ?? 1;
  const horizontal = opts.direction === "LR" || opts.direction === "RL";
  const inverted = opts.direction === "RL" || opts.direction === "BT";

  const axisSize = horizontal ? opts.nodeSize.width : opts.nodeSize.height;
  const perpSize = horizontal ? opts.nodeSize.height : opts.nodeSize.width;
  const nodeHeight = opts.nodeSize.height;
  const colStep = axisSize + phaseGapMultiplier * nodeHeight;
  const nodeSpacing = nodeSpacingMultiplier * nodeHeight;

  const nodeIds = new Set(nodes.map((n) => n.id));

  // ── 1. Happy path ──────────────────────────────────────────────────────────

  const happyPath = findHappyPath(nodeIds, edges, opts);

  // ── 2. Resolve ranks; sort HP activities along primary axis ───────────────

  const rankOf = (id: string): number => {
    const r = opts.rankByNode(id);
    return typeof r === "number" && !Number.isNaN(r) ? r : -1;
  };

  // Sort HP activities by temporal rank, falling back to frequency for ties.
  const hpOrdered = [...happyPath].sort((a, b) => {
    const dr = rankOf(a) - rankOf(b);
    if (dr !== 0) return dr;
    return opts.frequencyByNode(b) - opts.frequencyByNode(a);
  });

  // ── 3. Assign every node to a column ─────────────────────────────────────

  // columns[i] = list of node ids in that column (HP node always first)
  const columns: string[][] = hpOrdered.map((hp) => [hp]);

  if (hpOrdered.length === 0) {
    // No happy path: single column, all nodes sorted by frequency desc.
    const all = [...nodeIds].sort(
      (a, b) => opts.frequencyByNode(b) - opts.frequencyByNode(a),
    );
    columns.push(all);
  } else {
    const hpRanks = hpOrdered.map(rankOf);

    for (const id of nodeIds) {
      if (happyPath.has(id)) continue;

      const r = rankOf(id);
      let bestCol = 0;
      let bestDist = Infinity;

      for (let i = 0; i < hpRanks.length; i++) {
        // If no rank data (r === -1), assign to first column.
        const dist = r < 0 ? Infinity - i : Math.abs(hpRanks[i]! - r);
        if (dist < bestDist) {
          bestDist = dist;
          bestCol = i;
        }
      }
      columns[bestCol]!.push(id);
    }

    // Within each column: HP activity stays at index 0, then sort non-HP by
    // proximity to the column's HP rank (closest first) then by frequency.
    for (let i = 0; i < columns.length; i++) {
      const hpId = hpOrdered[i]!;
      const hpRank = rankOf(hpId);
      const nonHp = columns[i]!.slice(1).sort((a, b) => {
        const da = rankOf(a) < 0 ? Infinity : Math.abs(rankOf(a) - hpRank);
        const db = rankOf(b) < 0 ? Infinity : Math.abs(rankOf(b) - hpRank);
        if (da !== db) return da - db;
        return opts.frequencyByNode(b) - opts.frequencyByNode(a);
      });
      columns[i] = [hpId, ...nonHp];
    }
  }

  // ── 4. Position nodes ─────────────────────────────────────────────────────

  const positions = new Map<string, { x: number; y: number }>();

  for (let col = 0; col < columns.length; col++) {
    const list = columns[col]!;
    if (list.length === 0) continue;

    const axisPos = inverted
      ? (columns.length - 1 - col) * colStep
      : col * colStep;

    // HP node (index 0) always at perpendicular = 0.
    // Remaining nodes: placed symmetrically around 0.
    // Slot mapping: index 1 → slot +1, index 2 → slot -1, index 3 → slot +2, …
    for (let j = 0; j < list.length; j++) {
      let perpPos: number;
      if (j === 0) {
        perpPos = 0;
      } else {
        // 1→+1, 2→-1, 3→+2, 4→-2, …
        const half = Math.ceil(j / 2);
        perpPos = j % 2 === 1 ? half * (perpSize + nodeSpacing) : -half * (perpSize + nodeSpacing);
      }

      positions.set(
        list[j]!,
        horizontal ? { x: axisPos, y: perpPos } : { x: perpPos, y: axisPos },
      );
    }
  }

  const handles = DIRECTION_HANDLES[opts.direction];
  const positionedNodes = nodes.map((node) => {
    const pos = positions.get(node.id);
    return {
      ...node,
      position: pos ?? node.position ?? { x: 0, y: 0 },
      sourcePosition: handles.source,
      targetPosition: handles.target,
    };
  });

  return { nodes: positionedNodes, edges };
}
