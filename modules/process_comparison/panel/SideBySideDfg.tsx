"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  MarkerType,
  Position,
  useEdgesState,
  useNodesState,
  type Edge,
  type Node,
} from "@xyflow/react";

import { CanvasShell } from "@/components/visualizations/canvases/shared/canvas-shell";
import { formatNumber } from "@/lib/format";
import { useVizSettings } from "@/lib/stores/visualization-settings";

import { layeredPositions } from "./DiffDfgCanvas";
import type { DfgDiffData, DiffStatus } from "./types";

const NODE_W = 190;
const NODE_H = 52;

// Side identity matches the presence palette: baseline = blue, comparison = amber.
const SIDE_COLOR: Record<"a" | "b", string> = {
  a: "rgb(37, 99, 235)",
  b: "rgb(217, 119, 6)",
};

/** Render ONE log of the diff as a standalone single-log DFG (discovery style):
 *  keep the activities/edges present on that side and size edges by frequency.
 *  Two of these sit side by side so A and B can be read independently. */
export function SideBySideDfg({
  data,
  side,
  settings,
}: {
  data: DfgDiffData;
  side: "a" | "b";
  /** Popover body of the canvas control cluster (see `canvas-toolbar.tsx`). */
  settings?: ReactNode;
}) {
  const general = useVizSettings((s) => s.general);
  const { nodes: laidNodes, edges: laidEdges } = useMemo(() => {
    // An element is on side A unless it's comparison-only, and vice versa.
    const onSide = (s: DiffStatus) => (side === "a" ? s !== "only_b" : s !== "only_a");
    const freqOf = (n: { freq_a: number; freq_b: number }) =>
      side === "a" ? n.freq_a : n.freq_b;
    const color = SIDE_COLOR[side];

    const acts = data.activities.filter((a) => onSide(a.status));
    const eds = data.edges.filter((e) => onSide(e.status));
    const positions = layeredPositions(
      acts.map((a) => a.id),
      eds,
    );
    const maxFreq = eds.reduce((m, e) => Math.max(m, freqOf(e)), 1);

    const nodes: Node[] = acts.map((a) => ({
      id: a.id,
      position: positions.get(a.id) ?? { x: 0, y: 0 },
      data: {
        label: (
          <div className="flex flex-col items-center gap-0.5 px-1 text-center">
            <span className="truncate text-xs font-medium" style={{ maxWidth: NODE_W - 24 }}>
              {a.label}
            </span>
            <span className="text-[10px] text-muted-foreground tabular-nums">
              {formatNumber(freqOf(a))}
            </span>
          </div>
        ),
      },
      sourcePosition: Position.Bottom,
      targetPosition: Position.Top,
      style: {
        width: NODE_W,
        height: NODE_H,
        borderRadius: 8,
        border: `2px solid ${color}`,
        background: "var(--card)",
        color: "var(--foreground)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      },
    }));

    const edges: Edge[] = eds.map((e) => {
      const f = freqOf(e);
      const ratio = f / maxFreq;
      return {
        id: e.id,
        source: e.source,
        target: e.target,
        label: formatNumber(f),
        labelStyle: { fill: "var(--muted-foreground)", fontSize: 10 },
        labelBgPadding: [4, 2] as [number, number],
        labelBgBorderRadius: 4,
        labelBgStyle: { fill: "var(--card)", stroke: "var(--border)", strokeWidth: 1 },
        type: "default",
        style: { stroke: color, strokeWidth: 1 + 2.5 * ratio, opacity: 0.45 + 0.55 * ratio },
        markerEnd: { type: MarkerType.ArrowClosed, color },
      };
    });

    return { nodes, edges };
  }, [data, side]);

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  // Bumped by the toolbar "Reset layout" button → re-seeds from the computed
  // layered positions, discarding any in-session node drags.
  const [resetNonce, setResetNonce] = useState(0);
  useEffect(() => {
    setNodes(laidNodes);
    setEdges(laidEdges);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [laidNodes, laidEdges, resetNonce]);

  return (
    // The pane needs a real, non-zero height for React Flow to measure and fit
    // against. `h-[520px]` is an *arbitrary* Tailwind value used only inside a
    // module panel, and the web app's Tailwind build does not emit module-only
    // arbitrary utilities (only classes that also appear under apps/web survive,
    // which is why the overlay's `h-[640px]` works but this one didn't). The class
    // was silently dropped, the container collapsed to 0 height, and both maps
    // rendered invisibly. Pin the height with an inline style — which never
    // depends on the CSS build — and let CanvasShell fill it via the always-present
    // `h-full`.
    <div style={{ height: 520 }} className="w-full">
      <CanvasShell
        nodes={nodes}
        edges={edges}
        className="h-full w-full overflow-hidden rounded-xl border bg-card"
        fitViewKey={`${data.other_log_id}-${side}-${resetNonce}-${nodes.length}`}
        miniMap={general.showMinimap}
        showGrid={general.showGrid}
        settings={settings}
        onReset={() => setResetNonce((n) => n + 1)}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
      />
    </div>
  );
}
