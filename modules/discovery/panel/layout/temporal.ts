import { Position, type Edge, type Node } from "@xyflow/react";

/**
 * Strict-temporal layout: positions nodes along the primary axis by a rank
 * in [0, 1] (typically `mean_trace_position` from the discovery serializer)
 * and stacks them into perpendicular "lanes" to resolve overlaps.
 *
 * Reads like a Gantt chart for processes – earlier activities sit further
 * along the time axis. For highly cyclical DFGs the layered (Sugiyama) mode
 * still looks cleaner; this is the literal "x = mean trace position" view.
 *
 * Edges are returned unchanged. Caller should use a built-in xyflow edge
 * type (e.g. "smoothstep") since there are no ELK sections to render along.
 */

export interface TemporalLayoutOptions {
  /** Time axis. LR/RL → x; TB/BT → y. RL / BT inverts the rank direction. */
  direction: "LR" | "RL" | "TB" | "BT";
  nodeSize: { width: number; height: number };
  /** Gap between lanes (perpendicular to the time axis). */
  laneSpacing?: number;
  /** Gap enforced between two same-lane neighbours along the time axis. */
  axisGutter?: number;
  /** Total length along the time axis. Defaults to `max(900, nodes.length * 220)`. */
  axisLength?: number;
  /** rank ∈ [0, 1] for each node id; nodes missing a rank float to the end. */
  rankByNode: (nodeId: string) => number | undefined;
}

const DIRECTION_HANDLES: Record<TemporalLayoutOptions["direction"], { source: Position; target: Position }> = {
  LR: { source: Position.Right, target: Position.Left },
  RL: { source: Position.Left, target: Position.Right },
  TB: { source: Position.Bottom, target: Position.Top },
  BT: { source: Position.Top, target: Position.Bottom },
};

export function temporalLayout<TNodeData extends Record<string, unknown>, TEdgeData extends Record<string, unknown>>(
  nodes: Node<TNodeData>[],
  edges: Edge<TEdgeData>[],
  opts: TemporalLayoutOptions,
): { nodes: Node<TNodeData>[]; edges: Edge<TEdgeData>[] } {
  if (nodes.length === 0) return { nodes, edges };

  const horizontal = opts.direction === "LR" || opts.direction === "RL";
  const inverted = opts.direction === "RL" || opts.direction === "BT";
  const axisLength = opts.axisLength ?? Math.max(900, nodes.length * 220);
  const laneSpacing = opts.laneSpacing ?? 24;
  const axisGutter = opts.axisGutter ?? 28;

  const axisSize = horizontal ? opts.nodeSize.width : opts.nodeSize.height;
  const perpSize = horizontal ? opts.nodeSize.height : opts.nodeSize.width;

  // Resolve a usable rank for every node. Missing → 1 (end), inverted → 1-r.
  // We process nodes in temporal order so lane assignment only needs to look
  // at the last item per lane.
  const ranked = nodes.map((n) => {
    let r = opts.rankByNode(n.id);
    if (typeof r !== "number" || r !== r) r = 1;
    if (inverted) r = 1 - r;
    return { node: n, rank: Math.max(0, Math.min(1, r)) };
  });
  ranked.sort((a, b) => a.rank - b.rank);

  // Greedy lane packing: each lane tracks the right-edge of its last node;
  // a new node goes into the first lane whose last item ends before this
  // node starts (with `axisGutter` slack), or a fresh lane.
  const laneRightEdge: number[] = [];
  const positions = new Map<string, { x: number; y: number }>();

  for (const { node, rank } of ranked) {
    const center = rank * axisLength;
    const start = center - axisSize / 2;

    let laneIndex = laneRightEdge.findIndex((right) => right + axisGutter <= start);
    if (laneIndex < 0) {
      laneIndex = laneRightEdge.length;
      laneRightEdge.push(start + axisSize);
    } else {
      laneRightEdge[laneIndex] = start + axisSize;
    }

    const laneOffset = laneIndex * (perpSize + laneSpacing);
    const pos = horizontal
      ? { x: start, y: laneOffset }
      : { x: laneOffset, y: start };
    positions.set(node.id, pos);
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
