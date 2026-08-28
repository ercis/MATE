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

import type { DfgDiffData, DiffStatus } from "./types";

export type DfgColorMode = "presence" | "delta";

// Presence palette: baseline-only blue, comparison-only amber, shared neutral.
// Single source of truth for the canvas + the legend in index.tsx.
export const STATUS_COLOR: Record<DiffStatus, string> = {
  shared: "var(--muted-foreground)",
  only_a: "rgb(37, 99, 235)", // blue-600 – baseline
  only_b: "rgb(217, 119, 6)", // amber-600 – comparison
};

// Delta palette: green = more / only in B, red = less / only in A, neutral = flat.
export const DELTA_COLOR = {
  up: "rgb(22, 163, 74)", // green-600
  down: "rgb(220, 38, 38)", // red-600
  same: "var(--muted-foreground)",
};

/** Edge/node colour for the chosen mode. In delta mode a *shared* element is
 *  green when it grew (freq_b > freq_a), red when it shrank, neutral when flat. */
function colorFor(mode: DfgColorMode, status: DiffStatus, fa: number, fb: number): string {
  if (mode === "presence") return STATUS_COLOR[status];
  if (status === "only_b") return DELTA_COLOR.up;
  if (status === "only_a") return DELTA_COLOR.down;
  if (fb > fa) return DELTA_COLOR.up;
  if (fb < fa) return DELTA_COLOR.down;
  return DELTA_COLOR.same;
}

/** ` (Δ+3)` / ` (Δ-3)` suffix for shared elements in delta mode; empty otherwise. */
function deltaSuffix(mode: DfgColorMode, status: DiffStatus, fa: number, fb: number): string {
  if (mode !== "delta" || status !== "shared") return "";
  const d = fb - fa;
  if (d === 0) return "";
  return ` (Δ${d > 0 ? "+" : ""}${formatNumber(d)})`;
}

const NODE_W = 200;
const NODE_H = 56;
const X_GAP = 90;
const Y_GAP = 80;

/** Longest-path layering, top-to-bottom. Cycles are tolerated: rank relaxation
 *  is capped at |nodes| passes so a loop can't spin forever – it just settles
 *  at a stable layering. Exported so the side-by-side canvas reuses it. */
export function layeredPositions(
  nodeIds: string[],
  edges: { source: string; target: string }[],
): Map<string, { x: number; y: number }> {
  const rank = new Map<string, number>(nodeIds.map((id) => [id, 0]));
  const forward = edges.filter((e) => e.source !== e.target);
  for (let pass = 0; pass < nodeIds.length; pass++) {
    let changed = false;
    for (const e of forward) {
      const next = (rank.get(e.source) ?? 0) + 1;
      if (next > (rank.get(e.target) ?? 0)) {
        rank.set(e.target, next);
        changed = true;
      }
    }
    if (!changed) break;
  }

  const byRank = new Map<number, string[]>();
  for (const id of nodeIds) {
    const r = rank.get(id) ?? 0;
    (byRank.get(r) ?? byRank.set(r, []).get(r)!).push(id);
  }

  const pos = new Map<string, { x: number; y: number }>();
  for (const [r, ids] of byRank) {
    const rowWidth = ids.length * (NODE_W + X_GAP);
    ids.forEach((id, i) => {
      pos.set(id, {
        x: i * (NODE_W + X_GAP) - rowWidth / 2,
        y: r * (NODE_H + Y_GAP),
      });
    });
  }
  return pos;
}

export function DiffDfgCanvas({
  data,
  mode = "delta",
  settings,
}: {
  data: DfgDiffData;
  mode?: DfgColorMode;
  /** Popover body of the canvas control cluster (see `canvas-toolbar.tsx`) –
   *  where the layout / colour-mode switches live. */
  settings?: ReactNode;
}) {
  const general = useVizSettings((s) => s.general);
  const { nodes: laidNodes, edges: laidEdges } = useMemo(() => {
    const positions = layeredPositions(
      data.activities.map((a) => a.id),
      data.edges,
    );

    const nodes: Node[] = data.activities.map((a) => {
      const color = colorFor(mode, a.status, a.freq_a, a.freq_b);
      const freqLabel =
        a.status === "shared"
          ? `${formatNumber(a.freq_a)} → ${formatNumber(a.freq_b)}${deltaSuffix(mode, a.status, a.freq_a, a.freq_b)}`
          : a.status === "only_a"
            ? `${formatNumber(a.freq_a)} · baseline only`
            : `${formatNumber(a.freq_b)} · comparison only`;
      return {
        id: a.id,
        position: positions.get(a.id) ?? { x: 0, y: 0 },
        data: {
          label: (
            <div className="flex flex-col items-center gap-0.5 px-1 text-center">
              <span className="truncate text-xs font-medium" style={{ maxWidth: NODE_W - 24 }}>
                {a.label}
              </span>
              <span className="text-[10px] text-muted-foreground tabular-nums">{freqLabel}</span>
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
      };
    });

    const maxFreq = data.edges.reduce((m, e) => Math.max(m, e.freq_a, e.freq_b), 1);
    const edges: Edge[] = data.edges.map((e) => {
      const color = colorFor(mode, e.status, e.freq_a, e.freq_b);
      const weight = Math.max(e.freq_a, e.freq_b);
      const label =
        e.status === "shared"
          ? `${formatNumber(e.freq_a)} → ${formatNumber(e.freq_b)}${deltaSuffix(mode, e.status, e.freq_a, e.freq_b)}`
          : formatNumber(weight);
      return {
        id: e.id,
        source: e.source,
        target: e.target,
        label,
        labelStyle: { fill: "var(--muted-foreground)", fontSize: 10 },
        labelBgPadding: [4, 2] as [number, number],
        labelBgBorderRadius: 4,
        labelBgStyle: { fill: "var(--card)", stroke: "var(--border)", strokeWidth: 1 },
        type: "default",
        style: {
          stroke: color,
          strokeWidth: 1 + 2.5 * (weight / maxFreq),
          strokeDasharray: e.status === "shared" ? undefined : "6 4",
        },
        markerEnd: { type: MarkerType.ArrowClosed, color },
      };
    });

    return { nodes, edges };
  }, [data, mode]);

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
    <CanvasShell
      nodes={nodes}
      edges={edges}
      fitViewKey={`${data.baseline_log_id}-${data.other_log_id}-${mode}-${resetNonce}-${data.activities.length}`}
      miniMap={general.showMinimap}
      showGrid={general.showGrid}
      settings={settings}
      onReset={() => setResetNonce((n) => n + 1)}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
    />
  );
}
