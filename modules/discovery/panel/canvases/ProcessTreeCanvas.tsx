"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  MarkerType,
  useEdgesState,
  useNodesState,
  type Edge,
  type Node,
  type NodeMouseHandler,
} from "@xyflow/react";

import { treeLayout } from "../layout/tree";
import { mapEdgeType, truncate } from "../layout/direction";
import { PtLeafNode, type PtLeafNodeData } from "../nodes/pt-leaf-node";
import { PtOperatorNode, type PtOperatorNodeData } from "../nodes/pt-operator-node";
import type { ProcessTreeData, ProcessTreeNode } from "../types";
import { CanvasShell } from "@/components/visualizations/canvases/shared/canvas-shell";
import {
  useGeneralSettings,
  useNodePositions,
  usePersistNodePositions,
  useProcessTreeSettings,
} from "../discovery-settings-context";

const nodeTypes = { pt_operator: PtOperatorNode, pt_leaf: PtLeafNode } as const;

const NODE_W = 130;
const NODE_H = 40;
const LEVEL_GAP = 70;

interface LayoutAccumulator {
  nodes: Node[];
  edges: Edge[];
}

function flatten(
  node: ProcessTreeNode,
  positions: Map<string, { x: number; y: number }>,
  out: LayoutAccumulator,
  edgeType: "smoothstep" | "bezier" | "straight" | "step",
  depth: number,
  maxDepth: number | null,
  foldTau: boolean,
  labelMax: number,
  orientation: "vertical" | "horizontal",
  parent?: string,
) {
  if (maxDepth !== null && depth > maxDepth) return;
  if (foldTau && !node.operator && !node.label) return;

  const pos = positions.get(node.id) ?? { x: 0, y: 0 };
  const adjusted =
    orientation === "horizontal"
      ? { x: pos.y, y: pos.x - NODE_W / 2 }
      : { x: pos.x - NODE_W / 2, y: pos.y };

  if (node.operator) {
    const data: PtOperatorNodeData = { operator: node.operator };
    out.nodes.push({ id: node.id, type: "pt_operator", position: adjusted, data });
  } else {
    const label = node.label ? truncate(node.label, labelMax) : "τ";
    const data: PtLeafNodeData = { label, isInvisible: !node.label };
    out.nodes.push({ id: node.id, type: "pt_leaf", position: adjusted, data });
  }

  if (parent) {
    out.edges.push({
      id: `${parent}__${node.id}`,
      source: parent,
      target: node.id,
      type: edgeType,
      style: { stroke: "var(--muted-foreground)", strokeWidth: 1.2, opacity: 0.6 },
      markerEnd: { type: MarkerType.ArrowClosed, color: "var(--muted-foreground)" },
    });
  }

  for (const child of node.children) {
    flatten(child, positions, out, edgeType, depth + 1, maxDepth, foldTau, labelMax, orientation, node.id);
  }
}

interface ProcessTreeCanvasProps {
  data: ProcessTreeData;
}

export function ProcessTreeCanvas({ data }: ProcessTreeCanvasProps) {
  const general = useGeneralSettings();
  const [pt] = useProcessTreeSettings();
  const persistedPositions = useNodePositions("process_tree");
  const persist = usePersistNodePositions("process_tree");

  const { laidNodes, laidEdges, key } = useMemo(() => {
    const positions = treeLayout(data.root, {
      nodeSize: [NODE_W + 24, NODE_H + LEVEL_GAP],
      nonSiblingSeparation: 1.4,
    });
    const out: LayoutAccumulator = { nodes: [], edges: [] };
    flatten(
      data.root,
      positions,
      out,
      mapEdgeType(general.edgeRouting),
      0,
      pt.maxDepth,
      pt.foldTauLeaves,
      general.nodeLabelMaxLength,
      pt.orientation,
    );
    return { laidNodes: out.nodes, laidEdges: out.edges, key: out.nodes.length };
  }, [
    data,
    general.edgeRouting,
    general.nodeLabelMaxLength,
    pt.maxDepth,
    pt.foldTauLeaves,
    pt.orientation,
  ]);

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [seeded, setSeeded] = useState(false);

  useEffect(() => {
    const merged = laidNodes.map((n) => {
      const p = persistedPositions[n.id];
      return p ? { ...n, position: p } : n;
    });
    setNodes(merged);
    setEdges(laidEdges);
    setSeeded(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [laidNodes, laidEdges]);

  const onNodeDragStop = useCallback<NodeMouseHandler>(
    (_, node) => {
      persist({ [node.id]: { x: node.position.x, y: node.position.y } });
    },
    [persist],
  );

  if (!seeded) return null;
  return (
    <CanvasShell
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      fitViewKey={key}
      miniMap={general.showMinimap}
      showGrid={general.showGrid}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onNodeDragStop={onNodeDragStop}
    />
  );
}
