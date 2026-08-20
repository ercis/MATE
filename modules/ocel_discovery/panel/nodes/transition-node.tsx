"use client";

import { Handle, type Node, type NodeProps } from "@xyflow/react";

import { cn } from "@/lib/cn";

import { handlePositions, type Direction } from "./handle-positions";

export interface TransitionNodeData extends Record<string, unknown> {
  label: string;
  silent: boolean;
  direction: Direction;
}

export type TransitionNode = Node<TransitionNodeData, "transition">;

/** A Petri-net transition. Labelled transitions are activity boxes; silent
 *  (tau) transitions render as a small dark box. */
export function TransitionNode({ data, selected }: NodeProps<TransitionNode>) {
  const { source, target } = handlePositions(data.direction);

  if (data.silent) {
    return (
      <div className="relative">
        <Handle type="target" position={target} className="!h-2 !w-2 !border-0 !bg-muted-foreground" />
        <div
          className={cn(
            "h-7 w-12 rounded-md bg-foreground shadow-sm transition-all",
            selected && "ring-2 ring-primary ring-offset-2 ring-offset-background",
          )}
          title="τ (silent transition)"
        />
        <Handle type="source" position={source} className="!h-2 !w-2 !border-0 !bg-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="relative">
      <Handle type="target" position={target} className="!h-2 !w-2 !border-0 !bg-muted-foreground" />
      <div
        className={cn(
          "flex h-9 min-w-[120px] items-center justify-center rounded-md border bg-card px-3 text-sm font-medium shadow-sm transition-all",
          selected && "ring-2 ring-primary ring-offset-2 ring-offset-background",
        )}
        title={data.label}
      >
        <span className="truncate">{data.label}</span>
      </div>
      <Handle type="source" position={source} className="!h-2 !w-2 !border-0 !bg-muted-foreground" />
    </div>
  );
}
