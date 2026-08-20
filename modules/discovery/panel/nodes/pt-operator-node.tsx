"use client";

import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { GitBranch, GitMerge, Layers, Repeat, Workflow } from "lucide-react";

import { cn } from "@/lib/cn";
import type { ProcessTreeOperator } from "../types";

export interface PtOperatorNodeData extends Record<string, unknown> {
  operator: ProcessTreeOperator;
}

export type PtOperatorNode = Node<PtOperatorNodeData, "pt_operator">;

const OP_META: Record<ProcessTreeOperator, { label: string; icon: typeof Workflow; tint: string }> = {
  sequence: { label: "Sequence", icon: Workflow, tint: "bg-muted text-foreground" },
  xor: { label: "XOR", icon: GitBranch, tint: "bg-chart-4/25 text-foreground" },
  parallel: { label: "Parallel", icon: Layers, tint: "bg-chart-2/25 text-foreground" },
  loop: { label: "Loop", icon: Repeat, tint: "bg-chart-5/25 text-foreground" },
  or: { label: "OR", icon: GitMerge, tint: "bg-chart-3/25 text-foreground" },
};

export function PtOperatorNode({ data, selected }: NodeProps<PtOperatorNode>) {
  const meta = OP_META[data.operator];
  const Icon = meta.icon;
  return (
    <div className="relative">
      <Handle type="target" position={Position.Top} className="!h-2 !w-2 !border-0 !bg-muted-foreground" />
      <div
        className={cn(
          "flex h-9 items-center gap-2 rounded-full border px-3 shadow-sm transition-all cursor-pointer",
          "hover:-translate-y-0.5 hover:shadow-md hover:border-primary/40",
          selected && "ring-2 ring-primary ring-offset-2 ring-offset-background shadow-md",
          meta.tint,
        )}
      >
        <Icon className="h-3.5 w-3.5" />
        <span className="text-xs font-medium uppercase tracking-wide">{meta.label}</span>
      </div>
      <Handle type="source" position={Position.Bottom} className="!h-2 !w-2 !border-0 !bg-muted-foreground" />
    </div>
  );
}
