"use client";

import { Handle, type Node, type NodeProps } from "@xyflow/react";
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
}

export type ActivityNode = Node<ActivityNodeData, "activity">;

export function ActivityNode({ data, selected }: NodeProps<ActivityNode>) {
  const { layoutDirection } = useGeneralSettings();
  const { source, target } = handlePositions(layoutDirection);
  const intensity = Math.max(0, Math.min(1, data.intensity ?? 0));

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
