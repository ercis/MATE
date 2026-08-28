"use client";

import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { Play, Square } from "lucide-react";

import { cn } from "@/lib/cn";
import { formatNumber } from "@/lib/format";
import { useGeneralSettings } from "../discovery-settings-context";
import { handlePositions } from "./handle-positions";

export interface ActivityNodeData extends Record<string, unknown> {
  label: string;
  frequency: number;
  isStart?: boolean;
  isEnd?: boolean;
  /** Number of cases that begin at this activity. */
  startCount?: number;
  /** Number of cases that end at this activity. */
  endCount?: number;
  /** 0..1 – used by the performance DFG to tint the node by frequency. */
  intensity?: number;
  /** Sub-line under the label, e.g. "12 min" for performance edges' source nodes. */
  metric?: string;
  highlighted?: boolean;
  /** "vertical" pins handles to Top/Bottom regardless of the general layout
   *  direction — set by the vertical-only Process-flow layout so edge anchors
   *  (and the drag fallback bezier) stay correct even when the user's general
   *  direction is LR. */
  handleOrientation?: "vertical";
  /** "celonis" renders the 1:1 Celonis card skin (compact white card, leading
   *  ring, abbreviated count) — used by the "Process flow (Celonis)" mode. */
  variant?: "celonis";
  /** Abbreviated count (36.7K) for the celonis skin. */
  countLabel?: string;
  /** frequency / maxFrequency (0..1) — drives the KPI dot size. */
  freqRatio?: number;
}

export type ActivityNode = Node<ActivityNodeData, "activity">;

export function ActivityNode({ data, selected }: NodeProps<ActivityNode>) {
  const { layoutDirection } = useGeneralSettings();
  const { source, target } =
    data.handleOrientation === "vertical"
      ? { source: Position.Bottom, target: Position.Top }
      : handlePositions(layoutDirection);
  const intensity = Math.max(0, Math.min(1, data.intensity ?? 0));

  if (data.variant === "celonis") {
    // 1:1 Celonis card (measured: white, 1px border, radius 8, padding
    // 4/16/4/8, 20px leading ring, 13px title, 11px muted count).
    return (
      <div
        className={cn(
          "relative flex h-full w-full items-center gap-2 rounded-lg border bg-card shadow-sm transition-shadow cursor-pointer",
          "hover:shadow-md",
          selected && "ring-2 ring-primary ring-offset-1 ring-offset-background",
          data.highlighted && !selected && "ring-2 ring-primary/60 ring-offset-1 ring-offset-background",
        )}
        style={{ padding: "4px 16px 4px 8px" }}
      >
        <Handle type="target" position={target} className="!h-1.5 !w-1.5 !border-0 !bg-transparent" />
        <span
          className="flex shrink-0 items-center justify-center rounded-full"
          style={{
            width: 20,
            height: 20,
            background: "color-mix(in oklab, var(--dfg-edge, rgb(36, 148, 153)) 20%, transparent)",
          }}
        >
          {/* Celonis KPI dot: area encodes the activity's share of the max
              frequency (6–16px via sqrt so area ∝ ratio). */}
          <span
            className="rounded-full"
            style={{
              width: 6 + 10 * Math.sqrt(Math.max(0, Math.min(1, data.freqRatio ?? 0.3))),
              height: 6 + 10 * Math.sqrt(Math.max(0, Math.min(1, data.freqRatio ?? 0.3))),
              background: "var(--dfg-edge, rgb(36, 148, 153))",
            }}
          />
        </span>
        <div className="min-w-0 flex-1">
          <div
            className="overflow-hidden text-[13px] font-normal leading-[15px] text-foreground"
            title={data.label}
            style={{
              display: "-webkit-box",
              WebkitLineClamp: 2,
              WebkitBoxOrient: "vertical",
              overflowWrap: "anywhere",
            }}
          >
            {data.label}
          </div>
          <div className="text-[11px] font-medium leading-3 text-muted-foreground">
            {data.countLabel ?? formatNumber(data.frequency)}
          </div>
        </div>
        <Handle type="source" position={source} className="!h-1.5 !w-1.5 !border-0 !bg-transparent" />
      </div>
    );
  }

  return (
    <div
      className={cn(
        "relative rounded-xl border bg-card text-card-foreground shadow-sm transition-all cursor-pointer",
        "hover:-translate-y-0.5 hover:shadow-md hover:border-primary/40",
        selected && "ring-2 ring-primary ring-offset-2 ring-offset-background shadow-md",
        data.highlighted && !selected && "ring-2 ring-primary/60 ring-offset-2 ring-offset-background",
      )}
      style={{
        minWidth: 160,
        background:
          intensity > 0
            ? `color-mix(in oklab, var(--primary) ${Math.round(intensity * 30)}%, var(--card))`
            : undefined,
      }}
    >
      <Handle type="target" position={target} className="!h-2 !w-2 !border-0 !bg-muted-foreground" />

      <div className="px-3 py-2">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-medium leading-tight">{data.label}</div>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            {data.isStart && (
              <span
                className="inline-flex items-center gap-0.5 rounded-md border border-chart-2/40 bg-chart-2/15 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider text-foreground"
                title={`Cases start here${data.startCount ? `: ${formatNumber(data.startCount)}` : ""}`}
              >
                <Play className="h-2.5 w-2.5 fill-chart-2 text-chart-2" />
                <span className="tabular-nums">{formatNumber(data.startCount ?? 0)}</span>
              </span>
            )}
            {data.isEnd && (
              <span
                className="inline-flex items-center gap-0.5 rounded-md border border-chart-1/40 bg-chart-1/15 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider text-foreground"
                title={`Cases end here${data.endCount ? `: ${formatNumber(data.endCount)}` : ""}`}
              >
                <Square className="h-2.5 w-2.5 fill-chart-1 text-chart-1" />
                <span className="tabular-nums">{formatNumber(data.endCount ?? 0)}</span>
              </span>
            )}
          </div>
        </div>
        <div className="mt-0.5 text-[10px] tabular-nums text-muted-foreground">
          {data.metric ?? `${formatNumber(data.frequency)} events`}
        </div>
      </div>

      <Handle type="source" position={source} className="!h-2 !w-2 !border-0 !bg-muted-foreground" />
    </div>
  );
}
