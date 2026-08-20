import { Position, type Edge, type Node } from "@xyflow/react";

/**
 * Swimlane layout by process role: separates activities into three perpendicular
 * bands based on their start/end ratios:
 * - Entry: high proportion of trace starts (startCount / frequency >= entryThreshold)
 * - Exit: high proportion of trace ends (endCount / frequency >= exitThreshold)
 * - Core: intermediate activities (everything else)
 *
 * Within each band, nodes are positioned by mean_trace_position (primary axis)
 * with greedy lane-packing to resolve horizontal overlaps (perpendicular micro-lanes
 * within the band). This makes the "entry → core → exit" structure visually explicit.
 *
 * Edges are returned unchanged (caller uses built-in xyflow edge types).
 */

export interface TemporalSwimlaneOptions {
  direction: "LR" | "RL" | "TB" | "BT";
  nodeSize: { width: number; height: number };
  /** Fraction of total frequency that must be start-count to classify as Entry. Default: 0.3 */
  entryThreshold?: number;
  /** Fraction of total frequency that must be end-count to classify as Exit. Default: 0.3 */
  exitThreshold?: number;
  /** Gap between swimlane bands (perpendicular axis). Default: 48 */
  swimlaneGap?: number;
  /** Gap between micro-lanes within a band (perpendicular axis). Default: 16 */
  microLaneSpacing?: number;
  /** Gap enforced between same-lane neighbours along time axis. Default: 28 */
  axisGutter?: number;
  /** Total length along time axis. Default: max(900, nodes.length * 220) */
  axisLength?: number;
  rankByNode: (nodeId: string) => number | undefined;
  startCountByNode: (nodeId: string) => number;
  endCountByNode: (nodeId: string) => number;
  frequencyByNode: (nodeId: string) => number;
}

type SwimlaneRole = "entry" | "core" | "exit";

const DIRECTION_HANDLES: Record<TemporalSwimlaneOptions["direction"], { source: Position; target: Position }> = {
  LR: { source: Position.Right, target: Position.Left },
  RL: { source: Position.Left, target: Position.Right },
  TB: { source: Position.Bottom, target: Position.Top },
  BT: { source: Position.Top, target: Position.Bottom },
};

export function temporalSwimlaneLayout<TNodeData extends Record<string, unknown>, TEdgeData extends Record<string, unknown>>(
  nodes: Node<TNodeData>[],
  edges: Edge<TEdgeData>[],
  opts: TemporalSwimlaneOptions,
): { nodes: Node<TNodeData>[]; edges: Edge<TEdgeData>[] } {
  if (nodes.length === 0) return { nodes, edges };

  const entryThreshold = opts.entryThreshold ?? 0.3;
  const exitThreshold = opts.exitThreshold ?? 0.3;
  const swimlaneGap = opts.swimlaneGap ?? 48;
  const microLaneSpacing = opts.microLaneSpacing ?? 16;
  const axisGutter = opts.axisGutter ?? 28;
  const axisLength = opts.axisLength ?? Math.max(900, nodes.length * 220);

  const horizontal = opts.direction === "LR" || opts.direction === "RL";
  const inverted = opts.direction === "RL" || opts.direction === "BT";

  const axisSize = horizontal ? opts.nodeSize.width : opts.nodeSize.height;
  const perpSize = horizontal ? opts.nodeSize.height : opts.nodeSize.width;

  // Classify each node by role.
  const nodeRoles = new Map<string, SwimlaneRole>();
  for (const node of nodes) {
    const freq = Math.max(opts.frequencyByNode(node.id), 1);
    const startRatio = opts.startCountByNode(node.id) / freq;
    const endRatio = opts.endCountByNode(node.id) / freq;

    if (startRatio >= entryThreshold) {
      nodeRoles.set(node.id, "entry");
    } else if (endRatio >= exitThreshold) {
      nodeRoles.set(node.id, "exit");
    } else {
      nodeRoles.set(node.id, "core");
    }
  }

  // Group nodes by role and sort by rank (temporal position).
  const roleGroups: Record<SwimlaneRole, Array<{ node: Node<TNodeData>; rank: number }>> = {
    entry: [],
    core: [],
    exit: [],
  };

  for (const node of nodes) {
    const role = nodeRoles.get(node.id) ?? "core";
    let rank = opts.rankByNode(node.id);
    if (typeof rank !== "number" || rank !== rank) rank = 1;
    rank = Math.max(0, Math.min(1, inverted ? 1 - rank : rank));
    roleGroups[role].push({ node, rank });
  }

  // Sort each role group by rank.
  for (const role of ["entry", "core", "exit"] as const) {
    roleGroups[role].sort((a, b) => a.rank - b.rank);
  }

  // Simulate lane packing for each role to determine band heights.
  const bandMicroLaneCounts = {
    entry: 0,
    core: 0,
    exit: 0,
  };

  for (const role of ["entry", "core", "exit"] as const) {
    const laneRightEdge: number[] = [];
    for (const { rank } of roleGroups[role]) {
      const center = rank * axisLength;
      const start = center - axisSize / 2;
      const laneIndex = laneRightEdge.findIndex((right) => right + axisGutter <= start);
      if (laneIndex < 0) {
        laneRightEdge.push(start + axisSize);
      } else {
        laneRightEdge[laneIndex] = start + axisSize;
      }
    }
    bandMicroLaneCounts[role] = laneRightEdge.length;
  }

  // Compute band heights and offsets.
  // Height = N * nodeSize + (N-1) * spacing – the last lane has no trailing gap.
  const bandHeight = (count: number) =>
    count === 0 ? 0 : count * perpSize + (count - 1) * microLaneSpacing;

  const bandHeights = {
    entry: bandHeight(bandMicroLaneCounts.entry),
    core: bandHeight(bandMicroLaneCounts.core),
    exit: bandHeight(bandMicroLaneCounts.exit),
  };

  const bandStarts = {
    entry: 0,
    core: bandHeights.entry + (bandMicroLaneCounts.entry > 0 ? swimlaneGap : 0),
    exit: 0,
  };
  bandStarts.exit = bandStarts.core + bandHeights.core + (bandMicroLaneCounts.core > 0 ? swimlaneGap : 0);

  // Position nodes: greedy lane packing within each band.
  const positions = new Map<string, { x: number; y: number }>();

  for (const role of ["entry", "core", "exit"] as const) {
    const laneRightEdge: number[] = [];
    const bandStart = bandStarts[role];

    for (const { node, rank } of roleGroups[role]) {
      const center = rank * axisLength;
      const start = center - axisSize / 2;

      let laneIndex = laneRightEdge.findIndex((right) => right + axisGutter <= start);
      if (laneIndex < 0) {
        laneIndex = laneRightEdge.length;
        laneRightEdge.push(start + axisSize);
      } else {
        laneRightEdge[laneIndex] = start + axisSize;
      }

      const perpOffset = bandStart + laneIndex * (perpSize + microLaneSpacing);
      const pos = horizontal
        ? { x: start, y: perpOffset }
        : { x: perpOffset, y: start };
      positions.set(node.id, pos);
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
