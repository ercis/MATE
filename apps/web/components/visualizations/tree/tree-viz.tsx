"use client";

import { formatNumber } from "@/lib/format";
import type { TreeData, TreeNode, VizComponentProps } from "@/lib/visualizations/types";
import { VizEmpty } from "@/components/visualizations/viz-shell";

function Row({ node, depth }: { node: TreeNode; depth: number }) {
  const hasChildren = node.children.length > 0;
  return (
    <>
      <div
        className="flex items-center gap-1.5 border-l border-border/40 py-0.5 text-xs"
        style={{ paddingLeft: depth * 14 + 6 }}
      >
        <span className={hasChildren ? "font-medium" : "text-muted-foreground"}>
          {node.label || "·"}
        </span>
        {node.value != null && (
          <span className="text-[10px] tabular-nums text-muted-foreground">
            {formatNumber(node.value)}
          </span>
        )}
      </div>
      {node.children.map((c) => (
        <Row key={c.id} node={c} depth={depth + 1} />
      ))}
    </>
  );
}

/** Indented hierarchy for a `tree`-shaped dataset (process tree, prefix tree). */
export function TreeViz({ dataset }: VizComponentProps) {
  const data = dataset.shape === "tree" ? (dataset.data as TreeData) : null;
  if (!data) return <VizEmpty message={dataset.meta?.note} />;
  return (
    <div className="h-full w-full overflow-auto py-1">
      <Row node={data.root} depth={0} />
    </div>
  );
}
