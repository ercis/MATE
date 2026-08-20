"use client";

import { useEffect, useState } from "react";
import { MarkerType, useEdgesState, useNodesState, type Edge, type Node } from "@xyflow/react";

import { CanvasShell } from "@/components/visualizations/canvases/shared/canvas-shell";
import { CanvasLayoutSkeleton } from "@/components/visualizations/canvases/shared/canvas-skeleton";

import { elkLayout } from "../layout/layered";
import { PlaceNode } from "../nodes/place-node";
import { TransitionNode } from "../nodes/transition-node";
import type { OcpnNet } from "../queries";

const nodeTypes = { place: PlaceNode, transition: TransitionNode } as const;

/** Object-centric Petri net for a single object type: places (circles),
 *  transitions (activity boxes, silent ones dark), and arcs. Variable arcs –
 *  where an activity consumes / produces a variable number of objects – are
 *  drawn thicker, mirroring pm4py's own OCPN visualiser. */
export function OcpnCanvas({ net }: { net: OcpnNet | null }) {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [laid, setLaid] = useState(false);

  useEffect(() => {
    if (!net) return;
    let cancelled = false;

    const placeNodes: Node[] = net.places.map((p) => ({
      id: p.id,
      type: "place",
      position: { x: 0, y: 0 },
      data: {
        label: p.label,
        isInitial: p.is_initial,
        isFinal: p.is_final,
        direction: "LR",
      },
    }));

    const transitionNodes: Node[] = net.transitions.map((t) => ({
      id: t.id,
      type: "transition",
      position: { x: 0, y: 0 },
      data: {
        label: t.silent ? "τ" : t.label,
        silent: t.silent,
        direction: "LR",
      },
    }));

    const localEdges: Edge[] = net.arcs.map((arc) => ({
      id: arc.id,
      source: arc.source,
      target: arc.target,
      type: "smoothstep",
      style: {
        stroke: "var(--muted-foreground)",
        strokeWidth: arc.variable ? 3 : 1.5,
      },
      markerEnd: { type: MarkerType.ArrowClosed, color: "var(--muted-foreground)" },
    }));

    void elkLayout([...placeNodes, ...transitionNodes], localEdges, {
      direction: "RIGHT",
      edgeRouting: "ORTHOGONAL",
      nodeNode: 28,
      nodeNodeBetweenLayers: 80,
      nodeSizes: {
        place: { width: 36, height: 36 },
        transition: { width: 130, height: 36 },
      },
    }).then((result) => {
      if (cancelled) return;
      setNodes(result.nodes);
      setEdges(result.edges);
      setLaid(true);
    });

    return () => {
      cancelled = true;
    };
  }, [net, setNodes, setEdges]);

  if (!laid) return <CanvasLayoutSkeleton />;
  return (
    <CanvasShell
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      fitViewKey={`ocpn-${net?.object_type}-${nodes.length}`}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
    />
  );
}
