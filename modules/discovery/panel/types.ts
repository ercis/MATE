/**
 * JSON shapes returned by the discovery / performance modules. Mirror of the
 * Python serialisers in `modules/discovery/serializers.py` and
 * `modules/performance/module.py`.
 */

export interface DfgActivity {
  id: string;
  label: string;
  frequency: number;
  /** Mean normalised position (0..1) of this activity within its trace.
   *  Populated by the discovery serializer from v3 onwards; absent on
   *  older cached payloads. */
  mean_trace_position?: number;
}

export interface DfgEdge {
  id: string;
  source: string;
  target: string;
  frequency: number;
  performance_seconds?: number;
  dependency?: number | null;
}

export interface DfgData {
  kind: "dfg" | "dfg_performance" | "heuristics_net";
  activities: DfgActivity[];
  edges: DfgEdge[];
  start_activities: Record<string, number>;
  end_activities: Record<string, number>;
}

export interface PetriPlace {
  id: string;
  label: string;
  is_initial: boolean;
  is_final: boolean;
  tokens: number;
}

export interface PetriTransition {
  id: string;
  label: string;
  is_invisible: boolean;
  name: string;
}

export interface PetriArc {
  id: string;
  source: string;
  target: string;
  weight: number;
}

export interface PetriNetData {
  kind: "petri_net";
  places: PetriPlace[];
  transitions: PetriTransition[];
  arcs: PetriArc[];
}

export type ProcessTreeOperator = "sequence" | "xor" | "parallel" | "loop" | "or";

export interface ProcessTreeNode {
  id: string;
  operator: ProcessTreeOperator | null;
  label: string | null;
  children: ProcessTreeNode[];
}

export interface ProcessTreeData {
  kind: "process_tree";
  root: ProcessTreeNode;
}

export interface PrefixTreeNodeFlat {
  id: string;
  label: string | null;
  frequency: number;
  parent: string | null;
}

export interface PrefixTreeData {
  kind: "prefix_tree";
  nodes: PrefixTreeNodeFlat[];
}

export interface BpmnData {
  kind: "bpmn";
  version: number;
  /** Standard BPMN 2.0 XML. May lack BPMNDI (diagram interchange) coordinates
   *  when freshly derived by pm4py; `bpmn-auto-layout` fills them in
   *  client-side before bpmn-js renders. */
  xml: string;
}

// -- server-side DFG layout (POST /dfg/layout) --------------------------------

export type DfgLayoutAlgorithm = "backbone" | "backbone-v2" | "sugiyama";

/** Where an edge meets a node's border (backbone-v2). `u` is 0..1 along the
 *  face, left→right on top/bottom and top→bottom on left/right, so the client
 *  can rebuild the point from the LIVE rect and meet the border exactly. */
export interface DfgLayoutPort {
  face: "top" | "bottom" | "left" | "right";
  u: number;
  x: number;
  y: number;
}

/** Request body: the client-filtered VISIBLE subgraph (sliders + terminals). */
export interface DfgLayoutRequest {
  algorithm: DfgLayoutAlgorithm;
  nodes: { id: string; width: number; height: number }[];
  edges: [string, string][];
  start_id: string | null;
  end_id: string | null;
  params?: Record<string, number | boolean>;
}

/** One request edge, in request order, with its routed polyline. */
export interface DfgLayoutEdge {
  source: string;
  target: string;
  /** `backbone`/`sugiyama`: the virtual-node centers the edge routes through.
   *  `backbone-v2`: the interior vertices of the routed polyline. May be
   *  empty. */
  waypoints: [number, number][];
  self_loop: boolean;
  back_edge: boolean;
  bidirectional: boolean;
  // -- backbone-v2 only; absent for the other algorithms --------------------
  /** Ready-to-draw SVG path ("M … L … C …") in layout coordinates. */
  path?: string;
  /** The filleted skeleton the path was built from (hit-testing, debug). */
  polyline?: [number, number][];
  source_port?: DfgLayoutPort | null;
  target_port?: DfgLayoutPort | null;
  /** Arrowhead pose: x, y, angle in degrees. The server knows the true final
   *  tangent; deriving it from the last polyline point is wrong once the path
   *  is filleted. */
  arrow?: [number, number, number] | null;
  /** Label anchor: midpoint of the longest node-clear straight run. */
  label_at?: [number, number] | null;
  bends?: number;
  min_radius?: number | null;
}

export interface DfgLayoutResponse {
  kind: "dfg_layout";
  version: number;
  algorithm: DfgLayoutAlgorithm;
  /** Top-left corner per node id (React Flow convention). */
  x: Record<string, number>;
  y: Record<string, number>;
  rank: Record<string, number>;
  order: Record<string, number>;
  edges: DfgLayoutEdge[];
  /** Layout quality metrics (qm_be, qm_bal, qm_ec, qm_el, qm_eo, qm_no; plus
   *  qm_no_overlap, qm_bends, qm_straight_frac, qm_min_radius … for
   *  backbone-v2). */
  metrics: Record<string, number>;
  solver: { status: string; wall_ms: number; objective: number | null };
  wall_ms: number;
}
