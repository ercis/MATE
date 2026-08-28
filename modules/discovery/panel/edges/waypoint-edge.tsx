"use client";

import { BaseEdge, getBezierPath, useInternalNode, type EdgeProps } from "@xyflow/react";

import { renderLabel } from "./celonis-edge";
import {
  ARROW,
  arrowScaleFor,
  arrowTransform,
  hasDrifted,
  rectOf,
  renderSelfLoop,
  strokeOf,
  type ExpectedRects,
  type Point,
} from "./edge-common";

/**
 * Edge renderer for the server-computed layouts (Backbone / Sugiyama).
 *
 * The server returns per-edge `waypoints` — the virtual-node centers its rank
 * pipeline routed the edge through. This component draws a Catmull-Rom spline
 * source-port → waypoints → target-port, with the same drag contract as
 * `celonis-edge`: `data.expected` carries the layout-time node positions; once
 * a live node rect drifts from them (dragged, or a persisted position was
 * merged), the stale waypoints would pin the path to empty space, so it falls
 * back to a plain bezier until the next layout pass. Ports are derived from
 * the LIVE rects (bottom-center out, top-center in), so the spline meets the
 * node border exactly regardless of how the handles render.
 */

export interface WaypointEdgeData extends Record<string, unknown> {
  waypoints?: [number, number][];
  /** Layout-time node top-left positions (server response coordinates). */
  expected?: ExpectedRects;
  selfLoop?: boolean;
  /** Reverse edge exists: bow the curve so the pair doesn't overlap. */
  bidirectional?: boolean;
  backEdge?: boolean;
}

/** Catmull-Rom → cubic-bezier path through all points (uniform, tau = 6). */
function splinePath(points: Point[]): string {
  if (points.length < 2) return "";
  if (points.length === 2) {
    const [a, b] = points;
    const bend = Math.min(Math.max(Math.abs(b.y - a.y) / 2, 8), 60);
    return `M ${a.x} ${a.y} C ${a.x} ${a.y + bend}, ${b.x} ${b.y - bend}, ${b.x} ${b.y}`;
  }
  const parts = [`M ${points[0].x} ${points[0].y}`];
  for (let i = 0; i < points.length - 1; i++) {
    const p0 = points[i - 1] ?? points[i];
    const p1 = points[i];
    const p2 = points[i + 1];
    const p3 = points[i + 2] ?? p2;
    const c1x = p1.x + (p2.x - p0.x) / 6;
    const c1y = p1.y + (p2.y - p0.y) / 6;
    const c2x = p2.x - (p3.x - p1.x) / 6;
    const c2y = p2.y - (p3.y - p1.y) / 6;
    parts.push(`C ${c1x} ${c1y}, ${c2x} ${c2y}, ${p2.x} ${p2.y}`);
  }
  return parts.join(" ");
}

export function WaypointEdge(props: EdgeProps) {
  const {
    id,
    source,
    target,
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    style,
    label,
    labelStyle,
    labelBgPadding,
    labelBgBorderRadius,
    data,
  } = props;

  const edgeData = (data ?? {}) as WaypointEdgeData;
  // Hooks must run unconditionally; they're cheap subscriptions.
  const srcNode = useInternalNode(source);
  const tgtNode = useInternalNode(target);

  const { color: strokeColor, width: strokeWidth } = strokeOf(style);
  const arrowScale = arrowScaleFor(strokeWidth);

  // Self-loop: right-side arc built from the live node rect so it keeps
  // hugging the node during drags.
  if (edgeData.selfLoop && srcNode) {
    return renderSelfLoop({
      id,
      rect: rectOf(srcNode),
      style,
      strokeColor,
      arrowScale,
      label,
      labelStyle,
      labelBgPadding,
      labelBgBorderRadius,
    });
  }

  if (!srcNode || !tgtNode || hasDrifted(srcNode, tgtNode, edgeData.expected)) {
    const [path, labelX, labelY] = getBezierPath({
      sourceX,
      sourceY,
      targetX,
      targetY,
      sourcePosition,
      targetPosition,
    });
    return (
      <>
        <BaseEdge id={id} path={path} style={style} />
        {renderLabel({ label, labelX, labelY, labelStyle, labelBgPadding, labelBgBorderRadius })}
      </>
    );
  }

  const srcRect = rectOf(srcNode);
  const tgtRect = rectOf(tgtNode);
  const from: Point = { x: srcRect.x + srcRect.w / 2, y: srcRect.y + srcRect.h };
  const to: Point = { x: tgtRect.x + tgtRect.w / 2, y: tgtRect.y };
  const waypoints = (edgeData.waypoints ?? []).map(([x, y]) => ({ x, y }));
  const points: Point[] = [from, ...waypoints, to];

  // Bidirectional pair without a routed detour: bow deterministically so the
  // two opposing curves separate (lexicographic order fixes the side).
  let path: string;
  if (points.length === 2 && edgeData.bidirectional) {
    const bow = source < target ? 18 : -18;
    const mid: Point = { x: (from.x + to.x) / 2 + bow, y: (from.y + to.y) / 2 };
    path = splinePath([from, mid, to]);
  } else {
    path = splinePath(points);
  }

  const beforeTip = points.length >= 2 ? points[points.length - 2] : from;
  const labelAnchor = waypoints.length
    ? waypoints[Math.floor((waypoints.length - 1) / 2)]
    : { x: (from.x + to.x) / 2, y: (from.y + to.y) / 2 };

  return (
    <>
      <BaseEdge id={id} path={path} style={style} />
      <g transform={arrowTransform(beforeTip, to, arrowScale)} style={{ pointerEvents: "none" }}>
        <path d={ARROW} style={{ fill: strokeColor }} />
      </g>
      {renderLabel({
        label,
        labelX: labelAnchor.x,
        labelY: labelAnchor.y,
        labelStyle,
        labelBgPadding,
        labelBgBorderRadius,
      })}
    </>
  );
}
