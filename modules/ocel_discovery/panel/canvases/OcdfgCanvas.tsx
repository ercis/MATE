"use client";

import { useEffect, useState } from "react";
import { MarkerType, useEdgesState, useNodesState, type Edge, type Node } from "@xyflow/react";

import { CanvasShell } from "@/components/visualizations/canvases/shared/canvas-shell";
import { CanvasLayoutSkeleton } from "@/components/visualizations/canvases/shared/canvas-skeleton";
import { formatDuration, formatNumber } from "@/lib/format";

import { elkLayout } from "../layout/layered";
import { ActivityNode, type ActivityNodeData } from "../nodes/activity-node";
import { OCDFG_MEASURE_LABELS, type OcdfgData, type OcdfgMeasure } from "../queries";

const nodeTypes = { activity: ActivityNode } as const;

export type OcdfgMode = "frequency" | "performance";

/** Object-centric DFG for a single object type, rendered as a directed graph:
 *  activity nodes + directly-follows edges. In `frequency` mode edges are
 *  weighted/labelled by the selected measure (events / unique / total objects);
 *  in `performance` mode they are weighted/labelled by mean edge duration. */
export function OcdfgCanvas({
  data,
  objectType,
  measure = "unique_objects",
  mode = "frequency",
}: {
  data: OcdfgData;
  objectType: string | null;
  measure?: OcdfgMeasure;
  mode?: OcdfgMode;
}) {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [laid, setLaid] = useState(false);

  useEffect(() => {
    if (!objectType) return;
    let cancelled = false;

    const otEdges = data.edges.filter((e) => e.object_type === objectType);
    const otStart = data.start_activities.filter((a) => a.object_type === objectType);
    const otEnd = data.end_activities.filter((a) => a.object_type === objectType);

    const startCount = new Map(otStart.map((a) => [a.activity, a[measure]]));
    const endCount = new Map(otEnd.map((a) => [a.activity, a[measure]]));

    const measureLabel = OCDFG_MEASURE_LABELS[measure].toLowerCase();
    // Per-edge weight: the selected frequency measure, or mean duration in
    // performance mode (slower edges read thicker). Mirrors pm4py's annotated
    // OC-DFG which can be drawn by frequency or by performance.
    const edgeWeight = (e: OcdfgData["edges"][number]): number =>
      mode === "performance" ? (e.perf_mean ?? 0) : e[measure];
    const edgeLabel = (e: OcdfgData["edges"][number]): string =>
      mode === "performance" ? formatDuration(e.perf_mean) : formatNumber(e[measure]);

    const maxWeight = Math.max(1, ...otEdges.map(edgeWeight));

    // Volume per activity = the busiest incident edge / start / end value, used
    // for the muted sub-line. The OC-DFG payload has no per-activity total.
    const flow = new Map<string, number>();
    const bump = (act: string, c: number) => flow.set(act, Math.max(flow.get(act) ?? 0, c));
    for (const e of otEdges) {
      bump(e.source, e[measure]);
      bump(e.target, e[measure]);
    }
    for (const a of otStart) bump(a.activity, a[measure]);
    for (const a of otEnd) bump(a.activity, a[measure]);

    const activityNames = new Set<string>(flow.keys());

    const localNodes: Node<ActivityNodeData>[] = [...activityNames].map((act) => ({
      id: act,
      type: "activity",
      position: { x: 0, y: 0 },
      data: {
        label: act,
        sub: `${formatNumber(flow.get(act) ?? 0)} ${measureLabel}`,
        isStart: startCount.has(act),
        isEnd: endCount.has(act),
        startCount: startCount.get(act),
        endCount: endCount.get(act),
        direction: "LR",
      },
    }));

    const localEdges: Edge[] = otEdges.map((e) => ({
      id: `${e.source}→${e.target}`,
      source: e.source,
      target: e.target,
      type: "smoothstep",
      label: edgeLabel(e),
      labelStyle: { fill: "var(--muted-foreground)", fontSize: 10 },
      labelBgPadding: [4, 2] as [number, number],
      labelBgBorderRadius: 4,
      labelBgStyle: { fill: "var(--card)", stroke: "var(--border)", strokeWidth: 1 },
      style: {
        stroke: "var(--muted-foreground)",
        strokeWidth: 1 + (Math.log10(1 + edgeWeight(e)) / Math.log10(1 + maxWeight)) * 4,
      },
      markerEnd: { type: MarkerType.ArrowClosed, color: "var(--muted-foreground)" },
    }));

    void elkLayout(localNodes, localEdges, {
      direction: "RIGHT",
      edgeRouting: "ORTHOGONAL",
      nodeNode: 40,
      nodeNodeBetweenLayers: 90,
      nodeSizes: { activity: { width: 200, height: 60 } },
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
  }, [data, objectType, measure, mode, setNodes, setEdges]);

  if (!laid) return <CanvasLayoutSkeleton />;
  return (
    <CanvasShell
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      fitViewKey={`ocdfg-${objectType}-${measure}-${mode}-${nodes.length}`}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
    />
  );
}
