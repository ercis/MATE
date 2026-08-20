"use client";

import { useMemo } from "react";
import { MarkerType, Position, type Edge, type Node } from "@xyflow/react";

import { CanvasShell } from "@/components/visualizations/canvases/shared/canvas-shell";
import { formatNumber } from "@/lib/format";

import type { DfgDiffData, DiffStatus } from "./types";

// Baseline-only is blue, comparison-only is amber, shared is neutral. These are
// the single source of truth for the canvas + the legend in index.tsx.
export const STATUS_COLOR: Record<DiffStatus, string> = {
  shared: "var(--muted-foreground)",
  only_a: "rgb(37, 99, 235)", // blue-600 – baseline
  only_b: "rgb(217, 119, 6)", // amber-600 – comparison
};

const NODE_W = 200;
const NODE_H = 56;
const X_GAP = 90;
const Y_GAP = 80;

/** Longest-path layering, top-to-bottom. Cycles are tolerated: rank relaxation
 *  is capped at |nodes| passes so a loop can't spin forever – it just settles
 *  at a stable layering. */
function layeredPositions(
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

export function DiffDfgCanvas({ data }: { data: DfgDiffData }) {
  const { nodes, edges } = useMemo(() => {
    const positions = layeredPositions(
      data.activities.map((a) => a.id),
      data.edges,
    );

    const nodes: Node[] = data.activities.map((a) => {
      const color = STATUS_COLOR[a.status];
      const freqLabel =
        a.status === "shared"
          ? `${formatNumber(a.freq_a)} → ${formatNumber(a.freq_b)}`
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
      const color = STATUS_COLOR[e.status];
      const weight = Math.max(e.freq_a, e.freq_b);
      const label =
        e.status === "shared"
          ? `${formatNumber(e.freq_a)} → ${formatNumber(e.freq_b)}`
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
  }, [data]);

  return (
    <CanvasShell
      nodes={nodes}
      edges={edges}
      fitViewKey={`${data.baseline_log_id}-${data.other_log_id}-${data.activities.length}`}
    />
  );
}
