"use client";

import { useCallback, useEffect, useState } from "react";
import {
  MarkerType,
  useEdgesState,
  useNodesState,
  type Edge,
  type Node,
  type NodeMouseHandler,
} from "@xyflow/react";

import { formatNumber } from "@/lib/format";

import { elkLayout } from "../layout/layered";
import { mapDirection, mapEdgeRouting, mapEdgeType, truncate } from "../layout/direction";
import { ActivityNode, type ActivityNodeData } from "../nodes/activity-node";
import type { DfgData } from "../types";
import { CanvasShell } from "@/components/visualizations/canvases/shared/canvas-shell";
import { CanvasLayoutSkeleton } from "@/components/visualizations/canvases/shared/canvas-skeleton";
import {
  useGeneralSettings,
  useHeuristicsRenderSettings,
  useNodePositions,
  usePersistNodePositions,
} from "../discovery-settings-context";

const nodeTypes = { activity: ActivityNode } as const;

interface HeuristicsNetCanvasProps {
  data: DfgData;
}

export function HeuristicsNetCanvas({ data }: HeuristicsNetCanvasProps) {
  const general = useGeneralSettings();
  const [heur] = useHeuristicsRenderSettings();
  const positions = useNodePositions("heuristics");
  const persist = usePersistNodePositions("heuristics");

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [laid, setLaid] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const maxFreq = data.activities.reduce((m, a) => Math.max(m, a.frequency), 1);
    const maxEdgeFreq = data.edges.reduce((m, e) => Math.max(m, e.frequency), 1);
    const startSet = new Set(Object.keys(data.start_activities));
    const endSet = new Set(Object.keys(data.end_activities));

    const initialNodes: Node<ActivityNodeData>[] = data.activities.map((a) => ({
      id: a.id,
      type: "activity",
      position: { x: 0, y: 0 },
      data: {
        label: truncate(a.label, general.nodeLabelMaxLength),
        frequency: a.frequency,
        isStart: startSet.has(a.id),
        isEnd: endSet.has(a.id),
        intensity:
          general.theme === "monochrome"
            ? 0
            : (a.frequency / Math.max(maxFreq, 1)) * general.colorIntensity,
      },
    }));

    const edgeType = mapEdgeType(general.edgeRouting);

    const filteredEdges = heur.hideRareArcs
      ? data.edges.filter((e) => e.frequency / Math.max(maxEdgeFreq, 1) >= 0.1)
      : data.edges;

    const initialEdges: Edge[] = filteredEdges.map((e) => {
      const dep = e.dependency ?? null;
      const opacity = dep === null ? 0.6 : 0.3 + 0.7 * Math.max(0, Math.min(1, dep));
      let label: string | undefined;
      if (heur.edgeLabel === "count") {
        label = formatNumber(e.frequency);
      } else if (heur.edgeLabel === "dependency") {
        label = dep === null ? undefined : dep.toFixed(2);
      } else if (heur.edgeLabel === "both") {
        label = dep === null ? formatNumber(e.frequency) : `${formatNumber(e.frequency)} · ${dep.toFixed(2)}`;
      }
      return {
        id: e.id,
        source: e.source,
        target: e.target,
        type: edgeType,
        label,
        labelStyle: { fill: "var(--muted-foreground)", fontSize: 10 },
        labelBgPadding: [4, 2],
        labelBgBorderRadius: 4,
        labelBgStyle: { fill: "var(--card)", stroke: "var(--border)" },
        style: {
          stroke: "var(--muted-foreground)",
          strokeWidth: 1 + Math.log10(1 + e.frequency),
          opacity,
        },
        markerEnd: { type: MarkerType.ArrowClosed, color: "var(--muted-foreground)" },
      };
    });

    elkLayout(initialNodes, initialEdges, {
      direction: mapDirection(general.layoutDirection),
      edgeRouting: mapEdgeRouting(general.edgeRouting),
      nodeNode: 50,
      nodeNodeBetweenLayers: 120,
      defaultSize: { width: 200, height: 64 },
    }).then((result) => {
      if (cancelled) return;
      const merged = result.nodes.map((n) => {
        const p = positions[n.id];
        return p ? { ...n, position: p } : n;
      });
      setNodes(merged);
      setEdges(result.edges);
      setLaid(true);
    });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    data,
    general.layoutDirection,
    general.edgeRouting,
    general.nodeLabelMaxLength,
    general.colorIntensity,
    general.theme,
    heur.edgeLabel,
    heur.hideRareArcs,
  ]);

  const onNodeDragStop = useCallback<NodeMouseHandler>(
    (_, node) => {
      persist({ [node.id]: { x: node.position.x, y: node.position.y } });
    },
    [persist],
  );

  if (!laid) return <CanvasLayoutSkeleton />;
  return (
    <CanvasShell
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      fitViewKey={`hn-${data.activities.length}`}
      miniMap={general.showMinimap}
      showGrid={general.showGrid}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onNodeDragStop={onNodeDragStop}
    />
  );
}
