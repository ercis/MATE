"use client";

import { Handle, type Node, type NodeProps } from "@xyflow/react";
import { Play, Square } from "lucide-react";

import { cn } from "@/lib/cn";
import { formatNumber } from "@/lib/format";

import { handlePositions, type Direction } from "./handle-positions";

export interface ActivityNodeData extends Record<string, unknown> {
  label: string;
  /** Optional muted sub-line, e.g. "12 objects". */
  sub?: string;
  isStart?: boolean;
  isEnd?: boolean;
  /** Unique objects of the selected type that start / end at this activity. */
  startCount?: number;
  endCount?: number;
  direction: Direction;
}

export type ActivityNode = Node<ActivityNodeData, "activity">;

/** An OC-DFG activity box. Start / end markers show how many objects of the
 *  selected type begin / finish at this activity. */
export function ActivityNode({ data, selected }: NodeProps<ActivityNode>) {
  const { source, target } = handlePositions(data.direction);

  return (
    <div
      className={cn(
        "relative rounded-xl border bg-card text-card-foreground shadow-sm transition-all",
        selected && "ring-2 ring-primary ring-offset-2 ring-offset-background",
      )}
      style={{ minWidth: 160 }}
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
                title={`Objects start here: ${formatNumber(data.startCount ?? 0)}`}
              >
                <Play className="h-2.5 w-2.5 fill-chart-2 text-chart-2" />
                <span className="tabular-nums">{formatNumber(data.startCount ?? 0)}</span>
              </span>
            )}
            {data.isEnd && (
              <span
                className="inline-flex items-center gap-0.5 rounded-md border border-chart-1/40 bg-chart-1/15 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider text-foreground"
                title={`Objects end here: ${formatNumber(data.endCount ?? 0)}`}
              >
                <Square className="h-2.5 w-2.5 fill-chart-1 text-chart-1" />
                <span className="tabular-nums">{formatNumber(data.endCount ?? 0)}</span>
              </span>
            )}
          </div>
        </div>
        {data.sub && (
          <div className="mt-0.5 text-[10px] tabular-nums text-muted-foreground">{data.sub}</div>
        )}
      </div>

      <Handle type="source" position={source} className="!h-2 !w-2 !border-0 !bg-muted-foreground" />
    </div>
  );
}
