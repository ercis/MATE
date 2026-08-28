"use client";

/**
 * Rendered width of a node label, in px.
 *
 * A layout is only as good as the sizes it was given: a box that renders wider
 * than the slot ELK reserved sits on top of the channel ELK routed edges
 * through. Transition boxes are label-sized, so the canvas has to measure the
 * label before it can tell ELK how much space to keep free.
 *
 * Canvas `measureText` is used rather than a hidden DOM node because it is
 * synchronous and doesn't force a layout – this runs once per node per
 * re-layout, on the same main thread ELK is about to block.
 */

const FALLBACK_PX_PER_CHAR = 7.3;

let ctx: CanvasRenderingContext2D | null | undefined;
const cache = new Map<string, number>();

function context(): CanvasRenderingContext2D | null {
  if (ctx !== undefined) return ctx;
  if (typeof document === "undefined") {
    ctx = null;
    return ctx;
  }
  ctx = document.createElement("canvas").getContext("2d");
  if (ctx) {
    // Matches `transition-node.tsx`'s box: Tailwind `text-sm font-medium`.
    const family = getComputedStyle(document.body).fontFamily || "sans-serif";
    ctx.font = `500 14px ${family}`;
  }
  return ctx;
}

export function measureLabelWidth(text: string): number {
  const hit = cache.get(text);
  if (hit !== undefined) return hit;
  const c = context();
  const width = c ? c.measureText(text).width : text.length * FALLBACK_PX_PER_CHAR;
  cache.set(text, width);
  return width;
}
