"use client";

import { Handle, type Node, type NodeProps } from "@xyflow/react";

import { cn } from "@/lib/cn";

import { handlePositions, type Direction } from "./handle-positions";

export interface PlaceNodeData extends Record<string, unknown> {
  label: string;
  isInitial: boolean;
  isFinal: boolean;
  direction: Direction;
}

export type PlaceNode = Node<PlaceNodeData, "place">;

/** A Petri-net place – a small circle. Initial / final places get a ring and a
 *  token dot so the marking is legible without a legend. */
export function PlaceNode({ data, selected }: NodeProps<PlaceNode>) {
  const { source, target } = handlePositions(data.direction);
  const marked = data.isInitial || data.isFinal;
  return (
    <div className="relative">
      <Handle type="target" position={target} className="!h-2 !w-2 !border-0 !bg-muted-foreground" />
      <div
        className={cn(
          "flex h-9 w-9 items-center justify-center rounded-full border bg-card shadow-sm transition-all",
          selected && "ring-2 ring-primary ring-offset-2 ring-offset-background",
          !selected && marked && "ring-2 ring-foreground ring-offset-2 ring-offset-background",
        )}
        title={data.isInitial ? "Source place" : data.isFinal ? "Sink place" : data.label}
      >
        {marked && <span className="h-2 w-2 rounded-full bg-foreground" />}
      </div>
      <Handle type="source" position={source} className="!h-2 !w-2 !border-0 !bg-muted-foreground" />
    </div>
  );
}
