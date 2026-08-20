"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Handle,
  MarkerType,
  Position,
  useEdgesState,
  useNodesState,
  type Edge,
  type Node,
  type NodeMouseHandler,
  type NodeProps,
} from "@xyflow/react";

import { cn } from "@/lib/cn";
import { treeLayout } from "../layout/tree";
import { mapEdgeType } from "../layout/direction";
import type { PrefixTreeData, PrefixTreeNodeFlat } from "../types";
import { CanvasShell } from "@/components/visualizations/canvases/shared/canvas-shell";
import {
  useGeneralSettings,
  useNodePositions,
  usePersistNodePositions,
} from "../discovery-settings-context";

// ---------------------------------------------------------------------------
// Recursive node type (internal – rebuilt from the flat wire format)
// ---------------------------------------------------------------------------

interface PrefixTreeNode {
  id: string;
  label: string | null;
  frequency: number;
  children: PrefixTreeNode[];
}

function buildTree(flat: PrefixTreeNodeFlat[]): PrefixTreeNode | null {
  const byId = new Map<string, PrefixTreeNode>(
    flat.map((n) => [n.id, { id: n.id, label: n.label, frequency: n.frequency, children: [] }]),
  );
  let root: PrefixTreeNode | null = null;
  for (const n of flat) {
    if (n.parent === null) {
      root = byId.get(n.id)!;
    } else {
      byId.get(n.parent)?.children.push(byId.get(n.id)!);
    }
  }
  return root;
}

// ---------------------------------------------------------------------------
// Node type
// ---------------------------------------------------------------------------

interface PrefixNodeData extends Record<string, unknown> {
  label: string;
  frequency: number;
  isRoot: boolean;
}

type PrefixNode = Node<PrefixNodeData, "prefix_node">;

function PrefixNodeComponent({ data, selected }: NodeProps<PrefixNode>) {
  return (
    <div className="relative">
      <Handle
        type="target"
        position={Position.Top}
        className="!h-2 !w-2 !border-0 !bg-muted-foreground"
      />
      <div
        className={cn(
          "flex min-w-[88px] flex-col items-center justify-center rounded-md border bg-card px-2 py-1 text-xs shadow-sm",
          "cursor-pointer transition-all hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md",
          data.isRoot && "border-primary/30 bg-primary/5",
          selected && "ring-2 ring-primary ring-offset-2 ring-offset-background shadow-md",
        )}
      >
        <span className="truncate font-medium">{data.label}</span>
        <span className="text-[10px] tabular-nums text-muted-foreground">{data.frequency}</span>
      </div>
      <Handle
        type="source"
        position={Position.Bottom}
        className="!h-2 !w-2 !border-0 !bg-muted-foreground"
      />
    </div>
  );
}

const nodeTypes = { prefix_node: PrefixNodeComponent } as const;

// ---------------------------------------------------------------------------
// Flatten recursive tree → xyflow nodes + edges
// ---------------------------------------------------------------------------

interface Acc { nodes: Node[]; edges: Edge[] }

function flatten(
  node: PrefixTreeNode,
  positions: Map<string, { x: number; y: number }>,
  out: Acc,
  edgeType: "smoothstep" | "bezier" | "straight" | "step",
  parent?: string,
) {
  const raw = positions.get(node.id) ?? { x: 0, y: 0 };
  out.nodes.push({
    id: node.id,
    type: "prefix_node",
    position: { x: raw.x - 44, y: raw.y },
    data: { label: node.label ?? "Start", frequency: node.frequency, isRoot: node.label === null },
  });
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
    flatten(child, positions, out, edgeType, node.id);
  }
}

// ---------------------------------------------------------------------------
// Canvas
// ---------------------------------------------------------------------------

interface PrefixTreeCanvasProps {
  data: PrefixTreeData;
}

export function PrefixTreeCanvas({ data }: PrefixTreeCanvasProps) {
  const general = useGeneralSettings();
  const persistedPositions = useNodePositions("prefix_tree");
  const persist = usePersistNodePositions("prefix_tree");

  const { laidNodes, laidEdges, key } = useMemo(() => {
    const root = buildTree(data.nodes);
    if (!root) return { laidNodes: [], laidEdges: [], key: 0 };
    const positions = treeLayout(root, { nodeSize: [100, 65], nonSiblingSeparation: 1.1 });
    const out: Acc = { nodes: [], edges: [] };
    flatten(root, positions, out, mapEdgeType(general.edgeRouting));
    return { laidNodes: out.nodes, laidEdges: out.edges, key: out.nodes.length };
  }, [data, general.edgeRouting]);

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
    (_, node) => persist({ [node.id]: { x: node.position.x, y: node.position.y } }),
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
