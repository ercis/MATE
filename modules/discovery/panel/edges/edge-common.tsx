"use client";

import { BaseEdge, type EdgeProps, type InternalNode, type Node } from "@xyflow/react";

import { renderLabel } from "./celonis-edge";

/**
 * Shared pieces of the server-layout edge renderers (`waypoint-edge` for
 * Backbone/Sugiyama, `routed-edge` for Backbone v2).
 *
 * Both draw a server-computed route in layout coordinates and both must stop
 * doing so the moment a node moves — that contract lives here so the two
 * renderers cannot drift apart.
 */

export const DRIFT_TOLERANCE_PX = 1.5;
/** Measured Celonis arrow triangle: tip at the origin, body pointing back. */
export const ARROW = "M 0 0 L -9.746 9.997 L -9.74 -10.003 Z";

export type Point = { x: number; y: number };

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Layout-time node top-lefts, used to detect that a node has since moved. */
export interface ExpectedRects {
  sx: number;
  sy: number;
  tx: number;
  ty: number;
}

export function rectOf(node: InternalNode<Node>): Rect {
  return {
    x: node.internals.positionAbsolute.x,
    y: node.internals.positionAbsolute.y,
    w: node.measured.width ?? 220,
    h: node.measured.height ?? 59,
  };
}

export function arrowTransform(from: Point, to: Point, scale: number): string {
  const angle = (Math.atan2(to.y - from.y, to.x - from.x) * 180) / Math.PI;
  return `translate(${to.x} ${to.y}) rotate(${angle}) scale(${scale})`;
}

export function arrowTransformAt(x: number, y: number, angle: number, scale: number): string {
  return `translate(${x} ${y}) rotate(${angle}) scale(${scale})`;
}

/**
 * Has either endpoint moved away from where the layout put it? A dragged node
 * (or a merged persisted position) leaves the server route pinned to empty
 * space, so both renderers fall back to a plain bezier until the next pass.
 */
export function hasDrifted(
  srcNode: InternalNode<Node> | undefined,
  tgtNode: InternalNode<Node> | undefined,
  expected: ExpectedRects | undefined,
): boolean {
  if (!expected || !srcNode || !tgtNode) return true;
  return (
    Math.abs(srcNode.internals.positionAbsolute.x - expected.sx) > DRIFT_TOLERANCE_PX ||
    Math.abs(srcNode.internals.positionAbsolute.y - expected.sy) > DRIFT_TOLERANCE_PX ||
    Math.abs(tgtNode.internals.positionAbsolute.x - expected.tx) > DRIFT_TOLERANCE_PX ||
    Math.abs(tgtNode.internals.positionAbsolute.y - expected.ty) > DRIFT_TOLERANCE_PX
  );
}

export function strokeOf(style: EdgeProps["style"]): { color: string; width: number } {
  const css = style as React.CSSProperties | undefined;
  return {
    color: css?.stroke?.toString() ?? "var(--muted-foreground)",
    width: Number(css?.strokeWidth ?? 1.5),
  };
}

export function arrowScaleFor(strokeWidth: number): number {
  return Math.min(0.5 + strokeWidth / 12, 1);
}

interface SelfLoopArgs {
  id: string;
  rect: Rect;
  style: EdgeProps["style"];
  strokeColor: string;
  arrowScale: number;
  label: EdgeProps["label"];
  labelStyle: EdgeProps["labelStyle"];
  labelBgPadding: EdgeProps["labelBgPadding"];
  labelBgBorderRadius: EdgeProps["labelBgBorderRadius"];
}

/** Right-side arc built from the LIVE rect, so it keeps hugging during drags. */
export function renderSelfLoop({
  id,
  rect,
  style,
  strokeColor,
  arrowScale,
  label,
  labelStyle,
  labelBgPadding,
  labelBgBorderRadius,
}: SelfLoopArgs) {
  const right = rect.x + rect.w;
  const midY = rect.y + rect.h / 2;
  const excursion = 46;
  const path =
    `M ${right - 4} ${midY + rect.h * 0.28} ` +
    `C ${right + excursion} ${midY + rect.h * 0.9}, ` +
    `${right + excursion} ${midY - rect.h * 0.9}, ` +
    `${right - 4} ${midY - rect.h * 0.28}`;
  return (
    <>
      <BaseEdge id={id} path={path} style={style} />
      <g
        transform={arrowTransform(
          { x: right + excursion * 0.4, y: midY - rect.h * 0.6 },
          { x: right - 4, y: midY - rect.h * 0.28 },
          arrowScale,
        )}
        style={{ pointerEvents: "none" }}
      >
        <path d={ARROW} style={{ fill: strokeColor }} />
      </g>
      {renderLabel({
        label,
        labelX: right + excursion,
        labelY: midY,
        labelStyle,
        labelBgPadding,
        labelBgBorderRadius,
      })}
    </>
  );
}
