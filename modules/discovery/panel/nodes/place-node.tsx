"use client";

import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";

import { cn } from "@/lib/cn";
import { useGeneralSettings } from "../discovery-settings-context";

export interface PlaceNodeData extends Record<string, unknown> {
  label: string;
  isInitial: boolean;
  isFinal: boolean;
  tokens: number;
}

export type PlaceNode = Node<PlaceNodeData, "place">;

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

export function PlaceNode({ data, selected }: NodeProps<PlaceNode>) {
  const { layoutDirection } = useGeneralSettings();
  const { source, target } = handlePositions(layoutDirection);
  return (
    <div className="relative">
      <Handle type="target" position={target} className="!h-2 !w-2 !border-0 !bg-muted-foreground" />
      <div
        className={cn(
          "flex h-9 w-9 items-center justify-center rounded-full border bg-card shadow-sm transition-all cursor-pointer",
          "hover:scale-110 hover:shadow-md hover:border-primary/50",
          selected && "ring-2 ring-primary ring-offset-2 ring-offset-background",
          !selected && (data.isInitial || data.isFinal) && "ring-2 ring-foreground ring-offset-2 ring-offset-background",
        )}
        title={data.label}
      >
        {data.tokens > 0 && (
          <span className="h-2 w-2 rounded-full bg-foreground" />
        )}
      </div>
      <Handle type="source" position={source} className="!h-2 !w-2 !border-0 !bg-muted-foreground" />
    </div>
  );
}
