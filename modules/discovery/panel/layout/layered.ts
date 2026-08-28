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
  /**
   * Per-edge ELK layout options, e.g. straightness / direction priorities for
   * a dominant-path spine. Return `undefined` for edges without overrides.
   */
  edgeOptions?: (edge: Edge) => ElkOptions | undefined;
  /**
   * Per-node size; wins over `nodeSizes[node.type]` and `defaultSize`. Needed
   * when the rendered width depends on the data (a label-sized box), because a
   * size ELK didn't reserve is space it routes edges straight through.
   */
  nodeSize?: (node: Node) => NodeSize | undefined;
  /**
   * Stamp the size ELK was given onto `node.style.width/height`, so the DOM box
   * cannot disagree with the layout. Node components must then size themselves
   * `h-full w-full` – otherwise `measured.*` drifts from what was reserved and
   * every routed edge falls back to a plain bezier.
   */
  pinNodeSize?: boolean;
  /**
   * `elk.layered.mergeEdges`: edges out of one node share a trunk and branch
   * late, instead of each claiming its own point on the face.
   */
  mergeEdges?: boolean;
}

export interface Point {
  x: number;
  y: number;
}

/**
 * ELK's route for one edge, in the form an edge component can draw.
 *
 * `points` are absolute layout coordinates (same space as `child.x/child.y`);
 * the endpoint offsets let a renderer re-anchor them to the LIVE node rect, and
 * `expected` carries the layout-time node top-lefts so `hasDrifted` can tell a
 * stale route from a current one.
 *
 * NOTE: `bendPoints` only mean "polyline corners" under `ORTHOGONAL` routing.
 * Under `SPLINES` they are cubic control points (two per segment) and under
 * `POLYLINE` none are emitted at all, so a consumer that treats this as a
 * polyline must ask for `ORTHOGONAL`.
 */
export interface ElkRoute {
  points: Point[];
  /** First point relative to the source node's top-left. */
  sourceOffset: Point;
  /** Last point relative to the target node's top-left. */
  targetOffset: Point;
  /** Node top-lefts at layout time – matches `edge-common.ExpectedRects`. */
  expected: { sx: number; sy: number; tx: number; ty: number };
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
 * Returns a Promise, but the work is NOT off the main thread: `new ELK()`
 * (`elk.bundled.js`, below) uses elkjs' bundled "fake worker", which runs the
 * GWT solver *synchronously on the main thread* — a real Web Worker needs
 * `workerUrl`/`workerFactory`. So awaiting this still blocks for the solve;
 * callers defer the first call past first paint (see `runAfterPaint`) so the
 * loading skeleton stays responsive on large nets.
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

  // Crossing minimisation. LAYER_SWEEP is ELK's global barycenter optimiser and
  // the correct default. `semiInteractive` additionally pins the sweep to the
  // INPUT node order — meaningful only when the caller supplies an intentional
  // order via `nodeOrderHint`. Petri/Heuristics feed an arbitrary "all places
  // then all transitions" order; imposing that as a within-layer constraint on a
  // bipartite net manufactures crossings, so enable it ONLY alongside a hint.
  // Higher thoroughness keeps more sweep candidates (fewer crossings) at
  // negligible cost for the ≤200-node graphs these canvases produce.
  const crossingMin: ElkOptions = {
    "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
    "elk.layered.thoroughness": "30",
    ...(opts.nodeOrderHint ? { "elk.layered.crossingMinimization.semiInteractive": "true" } : {}),
  };

  // Give parallel edges and edge/node channels breathing room so orthogonal
  // segments don't coincide — directly targets the overlapping-edges symptom.
  const edgeSpacing: ElkOptions = {
    "elk.spacing.edgeEdge": "12",
    "elk.spacing.edgeNode": "16",
    "elk.layered.spacing.edgeEdgeBetweenLayers": "12",
    "elk.layered.spacing.edgeNodeBetweenLayers": "20",
  };

  const layoutOptions: ElkOptions = {
    "elk.algorithm": "layered",
    "elk.direction": direction,
    "elk.layered.spacing.nodeNodeBetweenLayers": String(opts.nodeNodeBetweenLayers ?? 80),
    "elk.spacing.nodeNode": String(opts.nodeNode ?? 40),
    "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
    "elk.layered.feedbackEdges": "true",
    "elk.edgeRouting": opts.edgeRouting ?? "ORTHOGONAL",
    ...(opts.mergeEdges ? { "elk.layered.mergeEdges": "true" } : {}),
    ...crossingMin,
    ...edgeSpacing,
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

  // No `ports` are declared: ELK layered synthesises one port per edge endpoint
  // on the flow-correct side and spreads them along the face itself, and
  // declaring them explicitly yields byte-identical output. (`elk.portConstraints`
  // is a node-target option that does not propagate from the root graph, so
  // setting it here never did anything either.)
  const sizeById = new Map<string, NodeSize>();
  const elkChildren: ElkNode[] = orderedNodes.map((node) => {
    const size = opts.nodeSize?.(node) ?? ((node.type && opts.nodeSizes?.[node.type]) || defaultSize);
    sizeById.set(node.id, size);
    return {
      id: node.id,
      width: size.width,
      height: size.height,
    };
  });

  const elkEdges: ElkExtendedEdge[] = edges.map((edge) => {
    const edgeLayoutOptions = opts.edgeOptions?.(edge);
    return {
      id: edge.id,
      sources: [edge.source],
      targets: [edge.target],
      ...(edgeLayoutOptions ? { layoutOptions: edgeLayoutOptions } : {}),
    };
  });

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

  // Capture ELK's edge sections. ELK routes every edge into its own channel
  // (`elk.spacing.edgeEdge` etc. above); throwing that away and letting xyflow's
  // built-in `smoothstep`/`bezier` re-invent a path between two centred handles
  // is what manufactures overlapping edges. `ElkEdge` draws these instead.
  const sectionsByEdge = new Map<string, Point[]>();
  for (const re of result.edges ?? []) {
    if (!re.id || !re.sections || re.sections.length === 0) continue;
    const points: Point[] = [];
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
    const size = sizeById.get(node.id);
    return {
      ...node,
      position: pos ?? node.position ?? { x: 0, y: 0 },
      sourcePosition: handles.source,
      targetPosition: handles.target,
      ...(opts.pinNodeSize && size
        ? { style: { ...(node.style ?? {}), width: size.width, height: size.height } }
        : {}),
    };
  });

  const positionedEdges = edges.map((edge) => {
    const points = sectionsByEdge.get(edge.id);
    const srcPos = positions.get(edge.source);
    const tgtPos = positions.get(edge.target);
    if (!points || points.length < 2 || !srcPos || !tgtPos) return edge;
    const first = points[0]!;
    const last = points[points.length - 1]!;
    const route: ElkRoute = {
      points,
      sourceOffset: { x: first.x - srcPos.x, y: first.y - srcPos.y },
      targetOffset: { x: last.x - tgtPos.x, y: last.y - tgtPos.y },
      expected: { sx: srcPos.x, sy: srcPos.y, tx: tgtPos.x, ty: tgtPos.y },
    };
    return {
      ...edge,
      data: { ...(edge.data ?? {}), elkRoute: route },
    } as unknown as Edge<TEdgeData>;
  });

  return { nodes: positionedNodes, edges: positionedEdges };
}
