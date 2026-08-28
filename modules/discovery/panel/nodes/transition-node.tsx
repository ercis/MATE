"use client";

import { useEffect } from "react";
import {
  Handle,
  useNodeId,
  useUpdateNodeInternals,
  type Node,
  type NodeProps,
} from "@xyflow/react";

import { cn } from "@/lib/cn";
import type { LayoutDirection } from "@/lib/stores/visualization-settings";
import { handlePositions } from "./handle-positions";

export interface TransitionNodeData extends Record<string, unknown> {
  label: string;
  isInvisible: boolean;
  direction: LayoutDirection;
}

export type TransitionNode = Node<TransitionNodeData, "transition">;

export function TransitionNode({ data, selected }: NodeProps<TransitionNode>) {
  const { source, target } = handlePositions(data.direction);

  // See `place-node.tsx`: a `<Handle>` that changes side does not by itself
  // invalidate xyflow's cached handle bounds.
  const id = useNodeId();
  const update = useUpdateNodeInternals();
  useEffect(() => {
    if (id) update(id);
  }, [id, data.direction, update]);

  if (data.isInvisible) {
    return (
      <div className="relative h-full w-full">
        <Handle type="target" position={target} className="!h-2 !w-2 !border-0 !bg-muted-foreground" />
        <div
          className={cn(
            "h-full w-full rounded-md bg-foreground shadow-sm cursor-pointer transition-all hover:scale-110 hover:shadow-md",
            selected && "ring-2 ring-primary ring-offset-2 ring-offset-background",
          )}
          title="τ (silent)"
        />
        <Handle type="source" position={source} className="!h-2 !w-2 !border-0 !bg-muted-foreground" />
      </div>
    );
  }
  return (
    // `h-full w-full` rather than `h-9 min-w-[120px]`: the canvas measures the
    // label and pins this wrapper to the width ELK reserved, so the box can no
    // longer grow past its slot and the `truncate` span below actually clips.
    <div className="relative h-full w-full">
      <Handle type="target" position={target} className="!h-2 !w-2 !border-0 !bg-muted-foreground" />
      <div
        className={cn(
          "flex h-full w-full items-center justify-center rounded-md border bg-card px-3 text-sm font-medium",
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
