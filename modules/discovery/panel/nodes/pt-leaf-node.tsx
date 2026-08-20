"use client";

import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";

import { cn } from "@/lib/cn";

export interface PtLeafNodeData extends Record<string, unknown> {
  label: string;
  /** When true the leaf is the silent τ marker. */
  isInvisible?: boolean;
}

export type PtLeafNode = Node<PtLeafNodeData, "pt_leaf">;

export function PtLeafNode({ data, selected }: NodeProps<PtLeafNode>) {
  if (data.isInvisible) {
    return (
      <div className="relative">
        <Handle type="target" position={Position.Top} className="!h-2 !w-2 !border-0 !bg-muted-foreground" />
        <div
          className={cn(
            "h-6 w-10 rounded-md bg-foreground shadow-sm cursor-pointer transition-all hover:scale-110 hover:shadow-md",
            selected && "ring-2 ring-primary ring-offset-2 ring-offset-background",
          )}
          title="τ"
        />
      </div>
    );
  }
  return (
    <div className="relative">
      <Handle type="target" position={Position.Top} className="!h-2 !w-2 !border-0 !bg-muted-foreground" />
      <div
        className={cn(
          "flex h-7 min-w-[110px] items-center justify-center rounded-md border bg-card px-3 text-xs font-medium",
          "shadow-sm transition-all cursor-pointer hover:-translate-y-0.5 hover:shadow-md hover:border-primary/40",
          selected && "ring-2 ring-primary ring-offset-2 ring-offset-background shadow-md",
        )}
      >
        <span className="truncate">{data.label}</span>
      </div>
    </div>
  );
}
