"use client";

import { BaseEdge, getBezierPath, useInternalNode, type EdgeProps } from "@xyflow/react";

import { renderLabel } from "./celonis-edge";
import {
  ARROW,
  arrowScaleFor,
  arrowTransformAt,
  hasDrifted,
  rectOf,
  renderSelfLoop,
  strokeOf,
  type ExpectedRects,
  type Point,
} from "./edge-common";

/**
 * Edge renderer for the Backbone v2 layout.
 *
 * Unlike `waypoint-edge`, this component computes no geometry. The server ran
 * an obstacle-aware router: it knows where every node is, which columns and
 * channels are free, and how large a corner radius the free space affords —
 * none of which the client has. So it ships a finished `M`/`L`/`C` path, the
 * arrowhead pose taken from the true final tangent, and a label anchor on a
 * node-clear straight run.
 *
 * The drag contract is `waypoint-edge`'s, unchanged: once a live node rect
 * drifts from `data.expected`, the route would be pinned to empty space, so it
 * falls back to a plain bezier until the next layout pass.
 */

export interface RoutedEdgeData extends Record<string, unknown> {
  /** Ready-to-draw SVG path in layout coordinates. */
  path?: string;
  /** The filleted skeleton behind the path (label fallback, debugging). */
  polyline?: [number, number][];
  /** Arrowhead pose from the server: x, y, angle in degrees. */
  arrow?: [number, number, number] | null;
  labelAt?: [number, number] | null;
  /** Layout-time node top-left positions (server response coordinates). */
  expected?: ExpectedRects;
  selfLoop?: boolean;
  backEdge?: boolean;
}

export function RoutedEdge(props: EdgeProps) {
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

  const edgeData = (data ?? {}) as RoutedEdgeData;
  // Hooks must run unconditionally; they're cheap subscriptions.
  const srcNode = useInternalNode(source);
  const tgtNode = useInternalNode(target);

  const { color: strokeColor, width: strokeWidth } = strokeOf(style);
  const arrowScale = arrowScaleFor(strokeWidth);

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

  const path = edgeData.path;
  if (!path || hasDrifted(srcNode, tgtNode, edgeData.expected)) {
    const [bezier, labelX, labelY] = getBezierPath({
      sourceX,
      sourceY,
      targetX,
      targetY,
      sourcePosition,
      targetPosition,
    });
    return (
      <>
        <BaseEdge id={id} path={bezier} style={style} />
        {renderLabel({ label, labelX, labelY, labelStyle, labelBgPadding, labelBgBorderRadius })}
      </>
    );
  }

  const polyline = edgeData.polyline ?? [];
  const anchor: Point = edgeData.labelAt
    ? { x: edgeData.labelAt[0], y: edgeData.labelAt[1] }
    : midpointOf(polyline, { x: (sourceX + targetX) / 2, y: (sourceY + targetY) / 2 });

  return (
    <>
      <BaseEdge id={id} path={path} style={style} />
      {edgeData.arrow ? (
        <g
          transform={arrowTransformAt(
            edgeData.arrow[0],
            edgeData.arrow[1],
            edgeData.arrow[2],
            arrowScale,
          )}
          style={{ pointerEvents: "none" }}
        >
          <path d={ARROW} style={{ fill: strokeColor }} />
        </g>
      ) : null}
      {renderLabel({
        label,
        labelX: anchor.x,
        labelY: anchor.y,
        labelStyle,
        labelBgPadding,
        labelBgBorderRadius,
      })}
    </>
  );
}

function midpointOf(polyline: [number, number][], fallback: Point): Point {
  if (polyline.length < 2) return fallback;
  const index = Math.floor((polyline.length - 1) / 2);
  const [ax, ay] = polyline[index];
  const [bx, by] = polyline[index + 1] ?? polyline[index];
  return { x: (ax + bx) / 2, y: (ay + by) / 2 };
}
