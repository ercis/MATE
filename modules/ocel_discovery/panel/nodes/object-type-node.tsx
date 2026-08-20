"use client";

import { Handle, type Node, type NodeProps } from "@xyflow/react";
import { Layers, RefreshCw } from "lucide-react";

import { cn } from "@/lib/cn";
import { formatNumber } from "@/lib/format";

import { handlePositions, type Direction } from "./handle-positions";

export interface ObjectTypeNodeData extends Record<string, unknown> {
  label: string;
  /** Number of objects of this type. */
  count: number;
  /** Within-type interactions (object pairs of the same type), if any. */
  intraCount?: number;
  direction: Direction;
}

export type ObjectTypeNode = Node<ObjectTypeNodeData, "objectType">;

/** A node in the object-type interaction graph: one object type, labelled with
 *  its object count and (when present) the number of within-type pairs. */
export function ObjectTypeNode({ data, selected }: NodeProps<ObjectTypeNode>) {
  const { source, target } = handlePositions(data.direction);
  const intra = data.intraCount ?? 0;

  return (
    <div
      className={cn(
        "relative rounded-xl border bg-card text-card-foreground shadow-sm transition-all",
        selected && "ring-2 ring-primary ring-offset-2 ring-offset-background",
      )}
      style={{ minWidth: 150 }}
    >
      <Handle type="target" position={target} className="!h-2 !w-2 !border-0 !bg-muted-foreground" />

      <div className="flex items-center gap-2 px-3 py-2">
        <Layers className="size-4 shrink-0 text-muted-foreground" />
        <div className="min-w-0">
          <div className="truncate text-sm font-medium">{data.label}</div>
          <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
            <span className="tabular-nums">{formatNumber(data.count)} objects</span>
            {intra > 0 && (
              <span className="inline-flex items-center gap-0.5 tabular-nums" title="within-type pairs">
                <RefreshCw className="size-3" />
                {formatNumber(intra)}
              </span>
            )}
          </div>
        </div>
      </div>

      <Handle type="source" position={source} className="!h-2 !w-2 !border-0 !bg-muted-foreground" />
    </div>
  );
}
