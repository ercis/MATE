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

export interface PlaceNodeData extends Record<string, unknown> {
  label: string;
  isInitial: boolean;
  isFinal: boolean;
  tokens: number;
  direction: LayoutDirection;
}

export type PlaceNode = Node<PlaceNodeData, "place">;

export function PlaceNode({ data, selected }: NodeProps<PlaceNode>) {
  const { source, target } = handlePositions(data.direction);

  // xyflow only re-measures `handleBounds` when the node's measured size
  // changes – moving a `<Handle>` to another side leaves the old bounds cached,
  // so the drag-fallback bezier would keep leaving from the pre-flip side.
  const id = useNodeId();
  const update = useUpdateNodeInternals();
  useEffect(() => {
    if (id) update(id);
  }, [id, data.direction, update]);

  return (
    // `h-full w-full`: the canvas pins this wrapper to exactly the size ELK was
    // told (`pinNodeSize`), and the box must not grow past it – a node wider
    // than its reserved slot sits on the channel ELK routed edges through.
    <div className="relative h-full w-full">
      <Handle type="target" position={target} className="!h-2 !w-2 !border-0 !bg-muted-foreground" />
      <div
        className={cn(
          "flex h-full w-full items-center justify-center rounded-full border bg-card shadow-sm transition-all cursor-pointer",
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
