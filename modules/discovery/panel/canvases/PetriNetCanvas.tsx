"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  useEdgesState,
  useNodesState,
  type Edge,
  type Node,
  type NodeMouseHandler,
} from "@xyflow/react";

import { runAfterPaint } from "../after-paint";
import { elkLayout } from "../layout/layered";
import { mapDirection, truncate } from "../layout/direction";
import { measureLabelWidth } from "../layout/text-width";
import { ElkEdge } from "../edges/elk-edge";
import { PlaceNode } from "../nodes/place-node";
import { TransitionNode } from "../nodes/transition-node";
import type { TransitionNodeData } from "../nodes/transition-node";
import type { PetriNetData } from "../types";
import { CanvasShell } from "@/components/visualizations/canvases/shared/canvas-shell";
import { CanvasLayoutSkeleton } from "@/components/visualizations/canvases/shared/canvas-skeleton";
import {
  useGeneralSettings,
  useNodePositions,
  usePersistNodePositions,
  usePetriSettings,
  useResetPositions,
} from "../discovery-settings-context";

const nodeTypes = { place: PlaceNode, transition: TransitionNode } as const;
const edgeTypes = { elk: ElkEdge } as const;

const PLACE_SIZE = { width: 36, height: 36 } as const;
/** Matches the `h-7 w-12` τ box in `transition-node.tsx`. */
const TAU_SIZE = { width: 48, height: 28 } as const;
const TRANSITION_HEIGHT = 36;
const TRANSITION_MIN_WIDTH = 120;
const TRANSITION_MAX_WIDTH = 240;
/** `px-3` on both sides plus a couple of px so the label never touches it. */
const TRANSITION_PADDING = 26;

/** Corner fillet for the routed arcs, from the user's edge-routing preference. */
function cornerRadiusFor(routing: string): number {
  if (routing === "straight") return 0;
  if (routing === "spline") return 24;
  return 10;
}

interface PetriNetCanvasProps {
  data: PetriNetData;
  /** Popover body of the canvas control cluster (see `canvas-toolbar.tsx`).
   *  Composed by the panel because it also holds the model choice. */
  settings?: ReactNode;
  /** A re-mine is in flight while the previous net stays on screen. */
  busy?: boolean;
}

export function PetriNetCanvas({ data, settings, busy }: PetriNetCanvasProps) {
  const general = useGeneralSettings();
  const [petri] = usePetriSettings();
  const direction = petri.layoutDirection;
  const positions = useNodePositions("petri");
  const persist = usePersistNodePositions("petri");
  const resetPositions = useResetPositions();

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [laid, setLaid] = useState(false);
  // First layout is deferred past first paint (skeleton) so elkjs' synchronous
  // main-thread solver doesn't block it; re-layouts run inline.
  const didFirstLayout = useRef(false);
  // Last ELK result BEFORE persisted drags are merged in – what "Reset layout"
  // puts back. `positions` is deliberately not an effect dep (it changes on
  // every drag persist and would re-run the solver), so clearing the store
  // alone leaves the dragged nodes on screen; reset has to re-seed too.
  const autoNodesRef = useRef<Node[]>([]);

  useEffect(() => {
    let cancelled = false;
    let cancelScheduled: (() => void) | undefined;

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
        direction,
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
        data: {
          label: truncate(label, general.nodeLabelMaxLength),
          isInvisible: t.is_invisible,
          direction,
        },
      };
    });

    const visibleNodeIds = new Set<string>([
      ...data.places.map((p) => p.id),
      ...visibleTransitionIds,
    ]);

    const cornerRadius = cornerRadiusFor(general.edgeRouting);
    const initialEdges: Edge[] = data.arcs
      .filter((arc) => visibleNodeIds.has(arc.source) && visibleNodeIds.has(arc.target))
      .map((arc) => ({
        id: arc.id,
        source: arc.source,
        target: arc.target,
        type: "elk",
        data: { cornerRadius },
        label: petri.showArcWeights && arc.weight > 1 ? String(arc.weight) : undefined,
        labelStyle: { fill: "var(--muted-foreground)", fontSize: 10 },
        labelBgPadding: [3, 2],
        labelBgBorderRadius: 4,
        labelBgStyle: { fill: "var(--card)", stroke: "var(--border)" },
        // No `markerEnd`: `ElkEdge` draws the arrowhead itself, because SVG's
        // `orient="auto"` reads its angle off the final sub-segment, which a
        // corner fillet can collapse.
        style: { stroke: "var(--muted-foreground)", strokeWidth: 1.5 },
      }));

    const runLayout = () => {
      didFirstLayout.current = true;
      elkLayout([...placeNodes, ...transitionNodes], initialEdges, {
        direction: mapDirection(direction),
        // Always ORTHOGONAL: `ElkEdge` reads the sections as a polyline, and
        // only this mode emits true right-angle corners (SPLINES returns bezier
        // control points, POLYLINE returns none and overshoots into the node).
        // The user's routing preference becomes the corner radius instead.
        edgeRouting: "ORTHOGONAL",
        mergeEdges: petri.mergeArcs,
        pinNodeSize: true,
        // `nodeNode` is the in-layer CROSS-axis gap, so one number can't serve
        // both directions: 28px between 36px-tall nodes reads fine in LR, and
        // is far too tight between ~200px-wide transitions in TB.
        nodeNode: direction === "TB" ? 48 : 28,
        nodeNodeBetweenLayers: direction === "TB" ? 64 : 80,
        nodeSize: (n) => {
          if (n.type === "place") return PLACE_SIZE;
          const d = n.data as TransitionNodeData;
          if (d.isInvisible) return TAU_SIZE;
          const width = Math.round(measureLabelWidth(d.label) + TRANSITION_PADDING);
          return {
            width: Math.min(Math.max(width, TRANSITION_MIN_WIDTH), TRANSITION_MAX_WIDTH),
            height: TRANSITION_HEIGHT,
          };
        },
      }).then((result) => {
        if (cancelled) return;
        autoNodesRef.current = result.nodes;
        const merged = result.nodes.map((n) => {
          const p = positions[n.id];
          return p ? { ...n, position: p } : n;
        });
        setNodes(merged);
        setEdges(result.edges);
        setLaid(true);
      });
    };

    if (didFirstLayout.current) runLayout();
    else cancelScheduled = runAfterPaint(runLayout);

    return () => {
      cancelled = true;
      cancelScheduled?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    data,
    direction,
    petri.mergeArcs,
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
      edgeTypes={edgeTypes}
      fitViewKey={`pn-${data.places.length}-${data.transitions.length}`}
      miniMap={general.showMinimap}
      showGrid={general.showGrid}
      busy={busy}
      settings={settings}
      onReset={() => {
        resetPositions("petri");
        setNodes([...autoNodesRef.current]);
      }}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onNodeDragStop={onNodeDragStop}
    />
  );
}
