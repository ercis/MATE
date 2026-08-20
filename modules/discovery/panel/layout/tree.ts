import { hierarchy, tree, type HierarchyNode } from "d3-hierarchy";

export interface TreeLayoutOptions {
  /** [horizontal node spacing, vertical level spacing]. Passed to `tree().nodeSize()`. */
  nodeSize?: [number, number];
  /** Multiplier applied to non-sibling separation. Larger values = roomier subtree gaps. */
  nonSiblingSeparation?: number;
}

/**
 * Lay out a process-tree hierarchy using d3-hierarchy's tidy-tree algorithm
 * (Reingold-Tilford / Buchheim). Properly balances subtrees with no
 * horizontal overlap – replaces the naive width-summing recursion that left
 * deep trees lopsided.
 */
export function treeLayout<T extends { id: string; children: T[] }>(
  root: T,
  opts: TreeLayoutOptions = {},
): Map<string, { x: number; y: number }> {
  const [hGap, vGap] = opts.nodeSize ?? [130, 90];
  const nonSibling = opts.nonSiblingSeparation ?? 1.4;

  const h = hierarchy<T>(root, (node) => node.children);
  const layout = tree<T>()
    .nodeSize([hGap, vGap])
    .separation((a, b) => (a.parent === b.parent ? 1 : nonSibling));

  const positioned = layout(h);

  const positions = new Map<string, { x: number; y: number }>();
  positioned.each((n: HierarchyNode<T> & { x?: number; y?: number }) => {
    if (typeof n.x === "number" && typeof n.y === "number") {
      positions.set(n.data.id, { x: n.x, y: n.y });
    }
  });
  return positions;
}
