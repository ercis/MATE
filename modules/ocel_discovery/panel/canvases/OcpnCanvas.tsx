"use client";

import { useEffect, useState } from "react";
import { MarkerType, useEdgesState, useNodesState, type Edge, type Node } from "@xyflow/react";

import { CanvasShell } from "@/components/visualizations/canvases/shared/canvas-shell";
import { CanvasLayoutSkeleton } from "@/components/visualizations/canvases/shared/canvas-skeleton";
import {
  CanvasSettings,
  CanvasSettingsSelect,
} from "@/components/visualizations/canvases/shared/canvas-toolbar";
import { EmptyState } from "@/components/empty-state";
import { Workflow } from "lucide-react";
import { useVizSettings } from "@/lib/stores/visualization-settings";

import { elkLayout } from "../layout/layered";
import { PlaceNode } from "../nodes/place-node";
import { TransitionNode } from "../nodes/transition-node";
import type { OcpnData } from "../queries";

const nodeTypes = { place: PlaceNode, transition: TransitionNode } as const;

/** Object-centric Petri net for a single object type: places (circles),
 *  transitions (activity boxes, silent ones dark), and arcs. Variable arcs –
 *  where an activity consumes / produces a variable number of objects – are
 *  drawn thicker, mirroring pm4py's own OCPN visualiser. */
export function OcpnCanvas({ data }: { data: OcpnData }) {
  const general = useVizSettings((s) => s.general);
  // The object-type picker is a canvas control – it lives in the settings
  // popover, so switching types keeps the popover open.
  const [ot, setOt] = useState<string | null>(null);
  const objectType = ot ?? data.object_types[0] ?? null;
  const net = data.nets.find((n) => n.object_type === objectType) ?? null;
  const empty = !net || net.places.length === 0;
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [laid, setLaid] = useState(false);
  // A re-layout (control change) keeps the current graph on screen – the
  // settings popover it was triggered from must not unmount.
  const [laying, setLaying] = useState(false);
  // Bumped by the toolbar "Reset layout" button → re-runs the ELK layout,
  // discarding any in-session node drags.
  const [resetNonce, setResetNonce] = useState(0);

  useEffect(() => {
    if (!net) return;
    let cancelled = false;
    setLaying(true);

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
      setLaying(false);
    });

    return () => {
      cancelled = true;
    };
  }, [net, resetNonce, setNodes, setEdges]);

  const settings = (
    <CanvasSettings>
      <CanvasSettingsSelect
        label="Object type"
        value={objectType ?? ""}
        onChange={setOt}
        options={data.object_types.map((t) => ({ value: t, label: t }))}
      />
    </CanvasSettings>
  );

  // No net for this type: keep the shell (and its picker) mounted and state
  // the reason over an empty canvas instead of swapping in a bare error card.
  if (empty) {
    return (
      <CanvasShell
        nodes={[]}
        edges={[]}
        miniMap={false}
        showGrid={general.showGrid}
        settings={settings}
        overlay={
          <div className="absolute inset-0 flex items-center justify-center">
            <EmptyState
              icon={Workflow}
              title="No Petri net for this object type"
              description="Too few events to mine a model. Pick another object type in the canvas settings."
            />
          </div>
        }
      />
    );
  }

  if (!laid) return <CanvasLayoutSkeleton />;
  return (
    <CanvasShell
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      fitViewKey={`ocpn-${net?.object_type}-${resetNonce}-${nodes.length}`}
      miniMap={general.showMinimap}
      showGrid={general.showGrid}
      busy={laying}
      settings={settings}
      onReset={() => setResetNonce((n) => n + 1)}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
    />
  );
}
