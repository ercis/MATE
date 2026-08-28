"use client";

import { BaseEdge, getBezierPath, useInternalNode, type EdgeProps } from "@xyflow/react";

import { renderLabel } from "./celonis-edge";
import {
  ARROW,
  arrowScaleFor,
  arrowTransform,
  hasDrifted,
  rectOf,
  strokeOf,
  type Point,
} from "./edge-common";
import type { ElkRoute } from "../layout/layered";

/**
 * Edge renderer for the ELK layered layouts (Petri net).
 *
 * ELK already routes every edge into its own channel – `elk.spacing.edgeEdge`
 * and friends exist precisely so parallel runs don't coincide – and returns the
 * result as section start/bend/end points. The built-in `smoothstep` edge
 * ignores all of it and re-derives a naive right-angle path between two centred
 * handles, which is what stacks arcs on top of each other. This component draws
 * ELK's actual route: straight runs joined by filleted corners.
 *
 * Drag contract is `waypoint-edge`'s, unchanged: `data.elkRoute.expected` holds
 * the layout-time node top-lefts, and once a live rect drifts from them the
 * route is pinned to empty space, so it falls back to a plain bezier until the
 * next layout pass.
 *
 * The arrowhead is drawn manually rather than via `markerEnd`, because SVG's
 * `orient="auto"` takes its angle from the final sub-segment – which a corner
 * fillet can collapse, snapping the arrow to a nonsense direction.
 */

export interface ElkEdgeData extends Record<string, unknown> {
  elkRoute?: ElkRoute;
  /** Corner fillet radius; 0 draws hard right angles. */
  cornerRadius?: number;
}

export function ElkEdge(props: EdgeProps) {
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

  const edgeData = (data ?? {}) as ElkEdgeData;
  // Hooks must run unconditionally; they're cheap subscriptions.
  const srcNode = useInternalNode(source);
  const tgtNode = useInternalNode(target);

  const { color: strokeColor, width: strokeWidth } = strokeOf(style);
  const arrowScale = arrowScaleFor(strokeWidth);
  const route = edgeData.elkRoute;

  if (!route || route.points.length < 2 || hasDrifted(srcNode, tgtNode, route.expected)) {
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

  // Re-anchor the endpoints to the LIVE rects. Anchoring to `sourceX/sourceY`
  // instead would drag the first point to the node's centred handle and break
  // the first orthogonal run – ELK's port sits off-centre by design, that
  // offset IS the fan-out that keeps sibling arcs apart.
  const srcRect = rectOf(srcNode!);
  const tgtRect = rectOf(tgtNode!);
  const points: Point[] = [
    { x: srcRect.x + route.sourceOffset.x, y: srcRect.y + route.sourceOffset.y },
    ...route.points.slice(1, -1),
    { x: tgtRect.x + route.targetOffset.x, y: tgtRect.y + route.targetOffset.y },
  ];

  const path = roundedPolyline(points, edgeData.cornerRadius ?? 10);
  const tip = points[points.length - 1]!;
  const beforeTip = points[points.length - 2]!;
  const anchor = midSegment(points);

  return (
    <>
      <BaseEdge id={id} path={path} style={style} />
      <g transform={arrowTransform(beforeTip, tip, arrowScale)} style={{ pointerEvents: "none" }}>
        <path d={ARROW} style={{ fill: strokeColor }} />
      </g>
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

/** Midpoint of the middle segment – a straight run, so the label sits clear. */
function midSegment(points: Point[]): Point {
  const i = Math.floor((points.length - 1) / 2);
  const a = points[i]!;
  const b = points[i + 1] ?? a;
  return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
}

/**
 * Straight `L` runs joined by a quadratic corner at each interior point. The
 * radius is clamped to half of the shorter adjacent run so a fillet can never
 * eat past the neighbouring bend; the closing `L` guarantees a clean tangent
 * at the target for the arrowhead.
 */
function roundedPolyline(points: Point[], cornerRadius: number): string {
  if (points.length === 0) return "";
  if (points.length === 1) return `M ${points[0]!.x} ${points[0]!.y}`;
  if (points.length === 2 || cornerRadius <= 0) {
    return points.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x} ${p.y}`).join(" ");
  }

  let d = `M ${points[0]!.x} ${points[0]!.y}`;
  for (let i = 1; i < points.length - 1; i++) {
    const prev = points[i - 1]!;
    const curr = points[i]!;
    const next = points[i + 1]!;

    const v1x = curr.x - prev.x;
    const v1y = curr.y - prev.y;
    const v2x = next.x - curr.x;
    const v2y = next.y - curr.y;
    const len1 = Math.hypot(v1x, v1y);
    const len2 = Math.hypot(v2x, v2y);

    if (len1 === 0 || len2 === 0) {
      d += ` L ${curr.x} ${curr.y}`;
      continue;
    }

    const r = Math.min(cornerRadius, len1 / 2, len2 / 2);
    const before = { x: curr.x - (v1x / len1) * r, y: curr.y - (v1y / len1) * r };
    const after = { x: curr.x + (v2x / len2) * r, y: curr.y + (v2y / len2) * r };

    d += ` L ${before.x} ${before.y} Q ${curr.x} ${curr.y} ${after.x} ${after.y}`;
  }
  d += ` L ${points[points.length - 1]!.x} ${points[points.length - 1]!.y}`;
  return d;
}
