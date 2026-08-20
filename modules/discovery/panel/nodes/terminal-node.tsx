"use client";

import { Handle, type Node, type NodeProps } from "@xyflow/react";
import { Play, Square } from "lucide-react";

import { cn } from "@/lib/cn";
import { useGeneralSettings } from "../discovery-settings-context";
import { handlePositions } from "./handle-positions";

export interface TerminalNodeData extends Record<string, unknown> {
  kind: "start" | "end";
  /** Total number of cases that start (or end) at the connected real activities. */
  caseCount?: number;
}

export type TerminalNode = Node<TerminalNodeData, "terminal">;

export function TerminalNode({ data }: NodeProps<TerminalNode>) {
  const { layoutDirection } = useGeneralSettings();
  const { source, target } = handlePositions(layoutDirection);
  const isStart = data.kind === "start";
  const Icon = isStart ? Play : Square;
  const label = isStart ? "Start" : "End";

  return (
    <div className="relative">
      {!isStart && (
        <Handle
          type="target"
          position={target}
          className="!h-2 !w-2 !border-0 !bg-muted-foreground"
        />
      )}
      <div
        className={cn(
          "flex h-9 items-center gap-2 rounded-full border px-4 shadow-sm transition-colors",
          isStart
            ? "border-chart-2/50 bg-chart-2/15 text-foreground"
            : "border-chart-1/50 bg-chart-1/15 text-foreground",
        )}
        title={isStart ? "Cases start here" : "Cases end here"}
      >
        <Icon
          className={cn(
            "h-3.5 w-3.5",
            isStart ? "fill-chart-2 text-chart-2" : "fill-chart-1 text-chart-1",
          )}
        />
        <span className="text-[11px] font-semibold uppercase tracking-wider">{label}</span>
        {typeof data.caseCount === "number" && data.caseCount > 0 && (
          <span className="text-[10px] tabular-nums text-muted-foreground">
            {data.caseCount}
          </span>
        )}
      </div>
      {isStart && (
        <Handle
          type="source"
          position={source}
          className="!h-2 !w-2 !border-0 !bg-muted-foreground"
        />
      )}
    </div>
  );
}
