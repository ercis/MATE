import { Position, type Edge, type Node } from "@xyflow/react";

/**
 * Phase-column layout: discretises mean_trace_position into N equally-spaced
 * phase buckets. Within each bucket/column, nodes are stacked along the
 * perpendicular axis sorted by frequency descending.
 *
 * Spacing is derived from node dimensions so the graph never looks cramped
 * or wasteful regardless of node count:
 *
 *   phaseGapMultiplier   – inter-column edge gap as a multiple of nodeSize.height
 *                          (e.g. 3 → gap = 3 × nodeHeight between adjacent columns)
 *   nodeSpacing          – within-column gap as a multiple of nodeSize.height
 *                          (defaults to 1 → gap = 1 × nodeHeight between stacked nodes)
 *
 * The total axis length is computed as:
 *   (phaseCount − 1) × (axisNodeSize + phaseGapMultiplier × nodeHeight)
 * so the inter-column gap is always exactly `phaseGapMultiplier × nodeHeight`.
 *
 * Edges are returned unchanged (caller uses built-in xyflow edge types).
 */

export interface TemporalPhasesOptions {
  direction: "LR" | "RL" | "TB" | "BT";
  nodeSize: { width: number; height: number };
  /** Number of phase buckets (columns / rows depending on direction). */
  phaseCount: number;
  /**
   * Gap between adjacent phase-column edges expressed as a multiple of
   * `nodeSize.height`. E.g. 3 → gap = 3 × nodeHeight.
   * Default: 3.
   */
  phaseGapMultiplier?: number;
  /**
   * Gap between nodes stacked within one phase column expressed as a
   * multiple of `nodeSize.height`. Default: 1.
   */
  nodeSpacingMultiplier?: number;
  /** Rank (0..1) for each node; undefined / NaN nodes go to the last phase. */
  rankByNode: (nodeId: string) => number | undefined;
  /** Frequency for each node; used to sort within a phase column. */
  frequencyByNode: (nodeId: string) => number;
}

const DIRECTION_HANDLES: Record<TemporalPhasesOptions["direction"], { source: Position; target: Position }> = {
  LR: { source: Position.Right, target: Position.Left },
  RL: { source: Position.Left, target: Position.Right },
  TB: { source: Position.Bottom, target: Position.Top },
  BT: { source: Position.Top, target: Position.Bottom },
};

export function temporalPhasesLayout<TNodeData extends Record<string, unknown>, TEdgeData extends Record<string, unknown>>(
  nodes: Node<TNodeData>[],
  edges: Edge<TEdgeData>[],
  opts: TemporalPhasesOptions,
): { nodes: Node<TNodeData>[]; edges: Edge<TEdgeData>[] } {
  if (nodes.length === 0) return { nodes, edges };

  const { phaseCount } = opts;
  const phaseGapMultiplier = opts.phaseGapMultiplier ?? 3;
  const nodeSpacingMultiplier = opts.nodeSpacingMultiplier ?? 1;

  const horizontal = opts.direction === "LR" || opts.direction === "RL";
  const inverted = opts.direction === "RL" || opts.direction === "BT";

  // axisSize  = node dimension along the primary (time) axis
  // perpSize  = node dimension along the perpendicular (stacking) axis
  const axisSize = horizontal ? opts.nodeSize.width : opts.nodeSize.height;
  const perpSize = horizontal ? opts.nodeSize.height : opts.nodeSize.width;

  // nodeHeight is always opts.nodeSize.height – used as the spacing base unit
  // so that the multipliers feel consistent regardless of layout direction.
  const nodeHeight = opts.nodeSize.height;

  const phaseGap = phaseGapMultiplier * nodeHeight;
  const nodeSpacing = nodeSpacingMultiplier * nodeHeight;

  // Total axis length: phases are spread so that the gap between adjacent
  // column edges is exactly `phaseGap`.
  //   colStep = axisSize + phaseGap
  //   axisLength = (phaseCount − 1) × colStep   (with a floor for 1-phase)
  const colStep = axisSize + phaseGap;
  const axisLength = phaseCount <= 1 ? axisSize : (phaseCount - 1) * colStep;

  // Assign each node to a phase bucket.
  const phases: Map<number, Node<TNodeData>[]> = new Map();
  for (let i = 0; i < phaseCount; i++) {
    phases.set(i, []);
  }

  for (const node of nodes) {
    let rank = opts.rankByNode(node.id);
    if (typeof rank !== "number" || rank !== rank) rank = 1;
    rank = Math.max(0, Math.min(1, rank));
    // Mirror for RL/BT so early activities land on the reading-direction start.
    if (inverted) rank = 1 - rank;
    const phase = Math.min(phaseCount - 1, Math.floor(rank * phaseCount));
    phases.get(phase)?.push(node);
  }

  // Sort nodes within each phase by frequency descending.
  for (const phase of phases.values()) {
    phase.sort((a, b) => opts.frequencyByNode(b.id) - opts.frequencyByNode(a.id));
  }

  // Column centers: 0, colStep, 2×colStep, …
  const colCenter = (phase: number) => (phaseCount <= 1 ? axisLength / 2 : phase * colStep);

  // Position nodes.
  const positions = new Map<string, { x: number; y: number }>();
  for (let phase = 0; phase < phaseCount; phase++) {
    const phaseNodes = phases.get(phase) ?? [];
    if (phaseNodes.length === 0) continue;

    const center = colCenter(phase);
    const axisStart = center - axisSize / 2;

    // Stack perpendicular to the time axis, centered on the perpendicular axis.
    const stackHeight =
      phaseNodes.length * perpSize + Math.max(0, phaseNodes.length - 1) * nodeSpacing;
    const perpStart = -stackHeight / 2;

    for (let j = 0; j < phaseNodes.length; j++) {
      const perpPos = perpStart + j * (perpSize + nodeSpacing);
      const pos = horizontal
        ? { x: axisStart, y: perpPos }
        : { x: perpPos, y: axisStart };
      positions.set(phaseNodes[j].id, pos);
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
