"use client";

import { useEffect, useState } from "react";
import { MarkerType, useEdgesState, useNodesState, type Edge, type Node } from "@xyflow/react";

import { CanvasShell } from "@/components/visualizations/canvases/shared/canvas-shell";
import { CanvasLayoutSkeleton } from "@/components/visualizations/canvases/shared/canvas-skeleton";
import { formatNumber } from "@/lib/format";

import { elkLayout } from "../layout/layered";
import { ObjectTypeNode, type ObjectTypeNodeData } from "../nodes/object-type-node";
import type { ObjectGraphData } from "../queries";

const nodeTypes = { objectType: ObjectTypeNode } as const;

/** Object-type-level relation graph: nodes are object types (with their object
 *  counts), edges are the number of distinct object pairs relating two types.
 *  Directed for descendants / inheritance; undirected (no arrowheads) for
 *  interaction / co-birth / co-death. */
export function ObjectGraphCanvas({ data }: { data: ObjectGraphData }) {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [laid, setLaid] = useState(false);

  useEffect(() => {
    let cancelled = false;

    const localNodes: Node<ObjectTypeNodeData>[] = data.object_types.map((t) => ({
      id: t.type,
      type: "objectType",
      position: { x: 0, y: 0 },
      data: { label: t.type, count: t.count, intraCount: t.intra_count, direction: "LR" },
    }));

    const maxCount = Math.max(1, ...data.edges.map((e) => e.count));

    const localEdges: Edge[] = data.edges.map((e) => ({
      id: `${e.source}→${e.target}`,
      source: e.source,
      target: e.target,
      type: "smoothstep",
      label: formatNumber(e.count),
      labelStyle: { fill: "var(--muted-foreground)", fontSize: 10 },
      labelBgPadding: [4, 2] as [number, number],
      labelBgBorderRadius: 4,
      labelBgStyle: { fill: "var(--card)", stroke: "var(--border)", strokeWidth: 1 },
      style: {
        stroke: "var(--muted-foreground)",
        strokeWidth: 1 + (Math.log10(1 + e.count) / Math.log10(1 + maxCount)) * 4,
      },
      markerEnd: e.directed
        ? { type: MarkerType.ArrowClosed, color: "var(--muted-foreground)" }
        : undefined,
    }));

    void elkLayout(localNodes, localEdges, {
      direction: "RIGHT",
      edgeRouting: "SPLINES",
      nodeNode: 40,
      nodeNodeBetweenLayers: 110,
      nodeSizes: { objectType: { width: 170, height: 56 } },
      celonisLike: true,
    }).then((result) => {
      if (cancelled) return;
      setNodes(result.nodes);
      setEdges(result.edges);
      setLaid(true);
    });

    return () => {
      cancelled = true;
    };
  }, [data, setNodes, setEdges]);

  if (!laid) return <CanvasLayoutSkeleton />;
  return (
    <CanvasShell
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      fitViewKey={`object-graph-${data.graph_type}-${nodes.length}`}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
    />
  );
}
