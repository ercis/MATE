"use client";

import { useMemo } from "react";
import {
  Background,
  Controls,
  Handle,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { useModules } from "@/lib/queries";
import { cn } from "@/lib/cn";

interface CapabilityGraphProps {
  focusedModuleId: string;
}

/**
 * Render `provides → consumes` edges across every installed module (§7.6.2).
 *
 * Each module is a node; an edge X → Y means "module Y consumes a capability
 * X provides". The focused module is highlighted so the user sees, at a
 * glance, what depends on this module and what it depends on.
 */
export function CapabilityGraph({ focusedModuleId }: CapabilityGraphProps) {
  const { data: modules } = useModules(null);

  const { nodes, edges } = useMemo(() => buildGraph(modules ?? [], focusedModuleId), [modules, focusedModuleId]);

  if (!modules || modules.length === 0) {
    return (
      <div className="rounded-md border border-dashed border-border px-4 py-6 text-center text-xs text-muted-foreground">
        No modules installed - nothing to graph yet.
      </div>
    );
  }
  if (edges.length === 0 && nodes.length === 1) {
    return (
      <div className="rounded-md border border-dashed border-border px-4 py-6 text-center text-xs text-muted-foreground">
        This is the only installed module. Capability links appear once another module declares <code className="rounded bg-muted px-1">consumes</code> for one of its <code className="rounded bg-muted px-1">provides</code>.
      </div>
    );
  }

  return (
    <div className="h-[280px] overflow-hidden rounded-md border bg-card">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        fitView
        proOptions={{ hideAttribution: true }}
        nodeTypes={NODE_TYPES}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        panOnDrag
        zoomOnScroll={false}
      >
        <Background gap={16} size={1} className="opacity-50" />
        <Controls showInteractive={false} className="!shadow-none" />
      </ReactFlow>
    </div>
  );
}

interface ModuleNodeData {
  label: string;
  focused: boolean;
  [key: string]: unknown;
}

function ModuleNode({ data }: NodeProps) {
  const d = data as ModuleNodeData;
  return (
    <div
      className={cn(
        "rounded-md border bg-card px-3 py-1.5 text-xs font-medium shadow-sm",
        d.focused && "border-primary bg-primary/10 text-primary",
      )}
    >
      <Handle type="target" position={Position.Left} className="!h-1.5 !w-1.5 !border-0 !bg-muted-foreground" />
      {d.label}
      <Handle type="source" position={Position.Right} className="!h-1.5 !w-1.5 !border-0 !bg-muted-foreground" />
    </div>
  );
}

const NODE_TYPES = { module: ModuleNode };

type Mod = {
  id: string;
  name: string;
  provides: string[];
  consumes: string[];
};

function buildGraph(modules: Mod[], focusedId: string): { nodes: Node[]; edges: Edge[] } {
  const byId = new Map(modules.map((m) => [m.id, m]));
  const provideMap = new Map<string, string>(); // capability → providing module id
  for (const m of modules) {
    for (const cap of m.provides) provideMap.set(cap, m.id);
  }
  const edges: Edge[] = [];
  for (const m of modules) {
    for (const cap of m.consumes) {
      const fromId = provideMap.get(cap);
      if (!fromId || fromId === m.id) continue;
      edges.push({
        id: `${fromId}->${m.id}:${cap}`,
        source: fromId,
        target: m.id,
        label: cap,
        labelStyle: { fontSize: 9 },
      });
    }
  }
  // Layered layout: providers on the left, consumers on the right. Simple
  // greedy ranking based on edges – good enough for the typical handful of
  // modules a single install has.
  const rank = new Map<string, number>();
  for (const m of modules) rank.set(m.id, 0);
  for (let i = 0; i < modules.length; i++) {
    for (const e of edges) {
      const fr = rank.get(e.source) ?? 0;
      const to = rank.get(e.target) ?? 0;
      if (to <= fr) rank.set(e.target, fr + 1);
    }
  }
  const groups = new Map<number, string[]>();
  for (const [id, r] of rank) {
    if (!groups.has(r)) groups.set(r, []);
    groups.get(r)!.push(id);
  }
  const nodes: Node[] = [];
  const x_gap = 220;
  const y_gap = 60;
  const ranks = [...groups.keys()].sort((a, b) => a - b);
  for (const r of ranks) {
    const ids = groups.get(r) ?? [];
    ids.forEach((id, i) => {
      const mod = byId.get(id);
      nodes.push({
        id,
        type: "module",
        position: { x: r * x_gap, y: i * y_gap },
        data: { label: mod?.name ?? id, focused: id === focusedId },
      });
    });
  }
  return { nodes, edges };
}
