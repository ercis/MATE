"use client";

import { useEffect, useState } from "react";
import { MarkerType, Position, type Edge, type Node } from "@xyflow/react";
import ELK, { type ElkExtendedEdge, type ElkNode } from "elkjs/lib/elk.bundled.js";

import { formatNumber } from "@/lib/format";
import type { GraphData, VizComponentProps } from "@/lib/visualizations/types";
import { VizEmpty } from "@/components/visualizations/viz-shell";
import { CanvasShell } from "@/components/visualizations/canvases/shared/canvas-shell";

const elk = new ELK();
const NODE_W = 160;
const NODE_H = 40;
const PLACE = 22;

const LAYOUT_OPTIONS = {
  "elk.algorithm": "layered",
  "elk.direction": "RIGHT",
  "elk.layered.spacing.nodeNodeBetweenLayers": "60",
  "elk.spacing.nodeNode": "28",
  "elk.edgeRouting": "ORTHOGONAL",
  "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
} as const;

async function layout(g: GraphData): Promise<{ nodes: Node[]; edges: Edge[] }> {
  const children: ElkNode[] = g.nodes.map((n) => ({
    id: n.id,
    width: n.kind === "place" ? PLACE : NODE_W,
    height: n.kind === "place" ? PLACE : NODE_H,
  }));
  const elkEdges: ElkExtendedEdge[] = g.edges.map((e) => ({
    id: e.id,
    sources: [e.source],
    targets: [e.target],
  }));
  const res = await elk.layout({ id: "root", layoutOptions: LAYOUT_OPTIONS, children, edges: elkEdges });
  const pos = new Map<string, { x: number; y: number }>();
  for (const c of res.children ?? []) {
    if (typeof c.x === "number" && typeof c.y === "number") pos.set(c.id, { x: c.x, y: c.y });
  }

  const maxFreq = Math.max(1, ...g.nodes.map((n) => n.value ?? 0));
  const nodes: Node[] = g.nodes.map((n) => {
    const isPlace = n.kind === "place";
    const t = (n.value ?? 0) / maxFreq;
    return {
      id: n.id,
      position: pos.get(n.id) ?? { x: 0, y: 0 },
      data: { label: n.label },
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
      // Read-only frequency map: CanvasShell enables drag/select by default, so
      // opt each node out to preserve the static, click-through feel.
      draggable: false,
      selectable: false,
      style: isPlace
        ? {
            width: PLACE,
            height: PLACE,
            borderRadius: "50%",
            background: "var(--muted)",
            border: "1px solid var(--border)",
          }
        : {
            width: NODE_W,
            height: NODE_H,
            borderRadius: 8,
            border: "1px solid var(--border)",
            background: `rgba(99,102,241,${0.06 + t * 0.5})`,
            fontSize: 11,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: "0 8px",
            textAlign: "center" as const,
            lineHeight: 1.1,
          },
    };
  });

  const maxEdge = Math.max(1, ...g.edges.map((e) => e.value ?? 0));
  const edges: Edge[] = g.edges.map((e) => ({
    id: e.id,
    source: e.source,
    target: e.target,
    selectable: false,
    label: e.value != null ? formatNumber(e.value) : undefined,
    markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 },
    style: { strokeWidth: 1 + 3 * ((e.value ?? 0) / maxEdge), stroke: "var(--muted-foreground)" },
    labelStyle: { fontSize: 9, fill: "var(--muted-foreground)" },
    labelBgStyle: { fill: "var(--card)", fillOpacity: 0.85 },
  }));
  return { nodes, edges };
}

/** Read-only node-link process map for a `graph`-shaped dataset (DFG, heuristics
 * net, Petri net), laid out with ELK and rendered with React Flow. Node shade =
 * frequency, edge width = transition frequency. */
export function ProcessMapViz({ dataset }: VizComponentProps) {
  const graph = dataset.shape === "graph" ? (dataset.data as GraphData) : null;
  // `dataset` is memoized per fetch by the card, so `graph` is a stable ref and
  // the layout effect runs once per data change (not every render).
  const [state, setState] = useState<{ nodes: Node[]; edges: Edge[] } | null>(null);

  useEffect(() => {
    let alive = true;
    if (!graph || graph.nodes.length === 0) {
      setState(null);
      return;
    }
    layout(graph)
      .then((r) => alive && setState(r))
      .catch(() => alive && setState(null));
    return () => {
      alive = false;
    };
  }, [graph]);

  if (!graph || graph.nodes.length === 0) {
    return <VizEmpty message={dataset.meta?.note ?? "No process model."} />;
  }
  if (!state) return <VizEmpty message="Laying out…" />;

  // Standard canvas shell: fit / zoom / fullscreen toolbar, idle-fade minimap
  // and dotted grid, all shared with every other graph canvas. The map is
  // read-only (nodes/edges opt out of drag + select in `layout`). No border/card
  // here — the viz card already frames it, so fill it edge-to-edge.
  return (
    <CanvasShell
      nodes={state.nodes}
      edges={state.edges}
      className="h-full w-full overflow-hidden"
      fitViewKey={state.nodes.length}
    />
  );
}
