import ELK, { type ElkExtendedEdge, type ElkNode, type LayoutOptions as ElkOptions } from "elkjs/lib/elk.bundled.js";
import { Position, type Edge, type Node } from "@xyflow/react";

interface NodeSize {
  width: number;
  height: number;
}

export interface LayeredOptions {
  /** Layout direction. Maps to `elk.direction`. */
  direction?: "RIGHT" | "DOWN" | "LEFT" | "UP";
  /** Edge routing style. ORTHOGONAL = right-angle channels (Petri); SPLINES = curved (DFG). */
  edgeRouting?: "ORTHOGONAL" | "POLYLINE" | "SPLINES";
  /** Spacing between nodes in the same layer. */
  nodeNode?: number;
  /** Spacing between layers (rank separation). */
  nodeNodeBetweenLayers?: number;
  /** Default node size used when a node has no `nodeSizes[type]` entry. */
  defaultSize?: NodeSize;
  /** Per-`node.type` size overrides, e.g. `{ place: { width: 36, height: 36 } }`. */
  nodeSizes?: Record<string, NodeSize>;
  /** Extra ELK options merged on top of the computed set. */
  extra?: ElkOptions;
  /**
   * Celonis-style preset: balanced node placement, network-simplex layering,
   * tighter port channels, higher thoroughness for cleaner edge routing,
   * connected-components compaction so disconnected subgraphs sit close.
   */
  celonisLike?: boolean;
  /**
   * Sort key per node id; lower values place earlier on the cross-axis.
   * Combined with `MODEL_ORDER` cycle-breaking, this also nudges ELK to
   * reverse edges that violate the hint, so feedback edges stay back-edges.
   */
  nodeOrderHint?: (nodeId: string) => number;
}

const elk = new ELK();

const DIRECTION_HANDLES: Record<NonNullable<LayeredOptions["direction"]>, { source: Position; target: Position }> = {
  RIGHT: { source: Position.Right, target: Position.Left },
  DOWN: { source: Position.Bottom, target: Position.Top },
  LEFT: { source: Position.Left, target: Position.Right },
  UP: { source: Position.Top, target: Position.Bottom },
};

/**
 * Layered layout using the Eclipse Layout Kernel (`elkjs`). Replaces dagre
 * with proper port placement, channelled edge routing, and Brandes-Köpf
 * crossing minimisation.
 *
 * Returns a Promise – ELK runs in a Web Worker on browsers that support it
 * and falls back to the main thread otherwise. Either way the call is
 * non-blocking from the caller's point of view.
 */
export async function elkLayout<TNodeData extends Record<string, unknown>, TEdgeData extends Record<string, unknown>>(
  nodes: Node<TNodeData>[],
  edges: Edge<TEdgeData>[],
  opts: LayeredOptions = {},
): Promise<{ nodes: Node<TNodeData>[]; edges: Edge<TEdgeData>[] }> {
  const direction = opts.direction ?? "RIGHT";
  const defaultSize = opts.defaultSize ?? { width: 180, height: 56 };

  const celonisOpts: ElkOptions = opts.celonisLike
    ? {
        // Network-simplex layering minimises edge length – what Celonis does.
        "elk.layered.layering.strategy": "NETWORK_SIMPLEX",
        // LEFTUP alignment: stricter than BALANCED (which averages four
        // candidate alignments and produces visible staircases).
        "elk.layered.nodePlacement.bk.fixedAlignment": "LEFTUP",
        // Pull weakly-connected components close together (no big gaps).
        "elk.layered.compaction.connectedComponents": "true",
        // Push port routing into proper channels – fewer overlapping edges.
        "elk.layered.spacing.edgeNodeBetweenLayers": "20",
        "elk.layered.spacing.edgeEdgeBetweenLayers": "12",
        "elk.spacing.edgeNode": "16",
        "elk.spacing.edgeEdge": "10",
        // High thoroughness pays off for ≤200-node graphs (DFG territory).
        "elk.layered.thoroughness": "30",
      }
    : {};

  // MODEL_ORDER cycle-breaking honours the input order as a tie-breaker for
  // which edges become back-edges, so a temporal `nodeOrderHint` keeps
  // feedback edges actually pointing backwards instead of cutting forward.
  const cycleBreaking: ElkOptions = opts.nodeOrderHint
    ? {
        "elk.layered.cycleBreaking.strategy": "MODEL_ORDER",
        "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
      }
    : opts.celonisLike
      ? { "elk.layered.cycleBreaking.strategy": "GREEDY" }
      : {};

  const layoutOptions: ElkOptions = {
    "elk.algorithm": "layered",
    "elk.direction": direction,
    "elk.layered.spacing.nodeNodeBetweenLayers": String(opts.nodeNodeBetweenLayers ?? 80),
    "elk.spacing.nodeNode": String(opts.nodeNode ?? 40),
    "elk.layered.crossingMinimization.semiInteractive": "true",
    "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
    "elk.layered.feedbackEdges": "true",
    "elk.portConstraints": "FIXED_SIDE",
    "elk.edgeRouting": opts.edgeRouting ?? "ORTHOGONAL",
    ...celonisOpts,
    ...cycleBreaking,
    ...opts.extra,
  };

  // ELK reads the input order as the model order (used by MODEL_ORDER cycle
  // breaking and `semiInteractive` crossing minimisation). Sorting here is
  // how the `nodeOrderHint` actually takes effect.
  const orderedNodes = opts.nodeOrderHint
    ? [...nodes].sort((a, b) => opts.nodeOrderHint!(a.id) - opts.nodeOrderHint!(b.id))
    : nodes;

  const elkChildren: ElkNode[] = orderedNodes.map((node) => {
    const size = (node.type && opts.nodeSizes?.[node.type]) || defaultSize;
    return {
      id: node.id,
      width: size.width,
      height: size.height,
    };
  });

  const elkEdges: ElkExtendedEdge[] = edges.map((edge) => ({
    id: edge.id,
    sources: [edge.source],
    targets: [edge.target],
  }));

  const root: ElkNode = {
    id: "root",
    layoutOptions,
    children: elkChildren,
    edges: elkEdges,
  };

  const result = await elk.layout(root);
  const positions = new Map<string, { x: number; y: number }>();
  for (const child of result.children ?? []) {
    if (typeof child.x === "number" && typeof child.y === "number") {
      positions.set(child.id, { x: child.x, y: child.y });
    }
  }

  // Capture ELK's edge sections so a custom xyflow edge component can render
  // through the bend points – that's how spline routing actually looks
  // Celonis-clean (xyflow's built-in bezier ignores ELK's intended path).
  const sectionsByEdge = new Map<string, { x: number; y: number }[]>();
  for (const re of result.edges ?? []) {
    if (!re.id || !re.sections || re.sections.length === 0) continue;
    const points: { x: number; y: number }[] = [];
    for (const s of re.sections) {
      points.push({ x: s.startPoint.x, y: s.startPoint.y });
      if (s.bendPoints) {
        for (const bp of s.bendPoints) points.push({ x: bp.x, y: bp.y });
      }
      points.push({ x: s.endPoint.x, y: s.endPoint.y });
    }
    sectionsByEdge.set(re.id, points);
  }

  const handles = DIRECTION_HANDLES[direction];
  const positionedNodes = nodes.map((node) => {
    const pos = positions.get(node.id);
    return {
      ...node,
      position: pos ?? node.position ?? { x: 0, y: 0 },
      sourcePosition: handles.source,
      targetPosition: handles.target,
    };
  });

  const positionedEdges = edges.map((edge) => {
    const points = sectionsByEdge.get(edge.id);
    if (!points) return edge;
    return {
      ...edge,
      data: { ...(edge.data ?? {}), elkPoints: points },
    } as unknown as Edge<TEdgeData>;
  });

  return { nodes: positionedNodes, edges: positionedEdges };
}
