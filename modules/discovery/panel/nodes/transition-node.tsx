"use client";

import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";

import { cn } from "@/lib/cn";
import { useGeneralSettings } from "../discovery-settings-context";

export interface TransitionNodeData extends Record<string, unknown> {
  label: string;
  isInvisible: boolean;
}

export type TransitionNode = Node<TransitionNodeData, "transition">;

function handlePositions(direction: "LR" | "TB" | "RL" | "BT") {
  switch (direction) {
    case "LR":
      return { target: Position.Left, source: Position.Right };
    case "RL":
      return { target: Position.Right, source: Position.Left };
    case "TB":
      return { target: Position.Top, source: Position.Bottom };
    case "BT":
      return { target: Position.Bottom, source: Position.Top };
  }
}

export function TransitionNode({ data, selected }: NodeProps<TransitionNode>) {
  const { layoutDirection } = useGeneralSettings();
  const { source, target } = handlePositions(layoutDirection);

  if (data.isInvisible) {
    return (
      <div className="relative">
        <Handle type="target" position={target} className="!h-2 !w-2 !border-0 !bg-muted-foreground" />
        <div
          className={cn(
            "h-7 w-12 rounded-md bg-foreground shadow-sm cursor-pointer transition-all hover:scale-110 hover:shadow-md",
            selected && "ring-2 ring-primary ring-offset-2 ring-offset-background",
          )}
          title="τ (silent)"
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
          "flex h-9 min-w-[120px] items-center justify-center rounded-md border bg-card px-3 text-sm font-medium",
          "shadow-sm transition-all cursor-pointer hover:-translate-y-0.5 hover:shadow-md hover:border-primary/40",
          selected && "ring-2 ring-primary ring-offset-2 ring-offset-background shadow-md",
        )}
      >
        <span className="truncate">{data.label}</span>
      </div>
      <Handle type="source" position={source} className="!h-2 !w-2 !border-0 !bg-muted-foreground" />
    </div>
  );
}
