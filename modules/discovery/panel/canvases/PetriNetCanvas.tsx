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

import { elkLayout } from "../layout/layered";
import { mapDirection, mapEdgeRouting, mapEdgeType, truncate } from "../layout/direction";
import { PlaceNode } from "../nodes/place-node";
import { TransitionNode } from "../nodes/transition-node";
import type { PetriNetData } from "../types";
import { CanvasShell } from "@/components/visualizations/canvases/shared/canvas-shell";
import { CanvasLayoutSkeleton } from "@/components/visualizations/canvases/shared/canvas-skeleton";
import {
  useGeneralSettings,
  useNodePositions,
  usePersistNodePositions,
  usePetriSettings,
} from "../discovery-settings-context";

const nodeTypes = { place: PlaceNode, transition: TransitionNode } as const;

interface PetriNetCanvasProps {
  data: PetriNetData;
}

export function PetriNetCanvas({ data }: PetriNetCanvasProps) {
  const general = useGeneralSettings();
  const [petri] = usePetriSettings();
  const positions = useNodePositions("petri");
  const persist = usePersistNodePositions("petri");

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [laid, setLaid] = useState(false);

  useEffect(() => {
    let cancelled = false;

    const visibleTransitions = petri.showInvisibleTransitions
      ? data.transitions
      : data.transitions.filter((t) => !t.is_invisible);
    const visibleTransitionIds = new Set(visibleTransitions.map((t) => t.id));

    const placeNodes: Node[] = data.places.map((p) => ({
      id: p.id,
      type: "place",
      position: { x: 0, y: 0 },
      data: {
        label: truncate(p.label, general.nodeLabelMaxLength),
        isInitial: petri.highlightMarkings ? p.is_initial : false,
        isFinal: petri.highlightMarkings ? p.is_final : false,
        tokens: petri.placeMode === "count" ? p.tokens : undefined,
      },
    }));

    const transitionNodes: Node[] = visibleTransitions.map((t) => {
      let label: string;
      if (petri.transitionLabelMode === "id") {
        label = t.name || t.id;
      } else if (petri.transitionLabelMode === "both") {
        label = t.label ? `${t.label} · ${t.name || t.id}` : t.name || t.id;
      } else {
        label = t.label || "τ";
      }
      return {
        id: t.id,
        type: "transition",
        position: { x: 0, y: 0 },
        data: { label: truncate(label, general.nodeLabelMaxLength), isInvisible: t.is_invisible },
      };
    });

    const visibleNodeIds = new Set<string>([
      ...data.places.map((p) => p.id),
      ...visibleTransitionIds,
    ]);

    const edgeType = mapEdgeType(general.edgeRouting);
    const initialEdges: Edge[] = data.arcs
      .filter((arc) => visibleNodeIds.has(arc.source) && visibleNodeIds.has(arc.target))
      .map((arc) => ({
        id: arc.id,
        source: arc.source,
        target: arc.target,
        type: edgeType,
        label: petri.showArcWeights && arc.weight > 1 ? String(arc.weight) : undefined,
        labelStyle: { fill: "var(--muted-foreground)", fontSize: 10 },
        labelBgPadding: [3, 2],
        labelBgBorderRadius: 4,
        labelBgStyle: { fill: "var(--card)", stroke: "var(--border)" },
        style: { stroke: "var(--muted-foreground)", strokeWidth: 1.5 },
        markerEnd: { type: MarkerType.ArrowClosed, color: "var(--muted-foreground)" },
      }));

    elkLayout([...placeNodes, ...transitionNodes], initialEdges, {
      direction: mapDirection(general.layoutDirection),
      edgeRouting: mapEdgeRouting(general.edgeRouting),
      nodeNode: 28,
      nodeNodeBetweenLayers: 80,
      nodeSizes: {
        place: { width: 36, height: 36 },
        transition: { width: 130, height: 36 },
      },
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
    petri.showInvisibleTransitions,
    petri.transitionLabelMode,
    petri.placeMode,
    petri.highlightMarkings,
    petri.showArcWeights,
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
      fitViewKey={`pn-${data.places.length}-${data.transitions.length}`}
      miniMap={general.showMinimap}
      showGrid={general.showGrid}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onNodeDragStop={onNodeDragStop}
    />
  );
}
