"use client";

import {
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  type EdgeProps,
} from "@xyflow/react";

interface ElkPoint {
  x: number;
  y: number;
}

export interface ElkSplineEdgeData extends Record<string, unknown> {
  elkPoints?: ElkPoint[];
}

/**
 * Custom xyflow edge that renders along ELK-computed bend points.
 *
 * - When ELK has produced a path, draw a smooth Catmull-Rom-flavoured curve
 *   through the bend points (gives splines that hug the routing channels
 *   ELK chose, not stray off through other nodes).
 * - When the user has dragged the source or target node, the captured points
 *   are stale – fall back to xyflow's plain bezier so the arrow still tracks
 *   the new handle positions.
 *
 * Arrow head is rendered manually (instead of via SVG `marker-end` +
 * `orient="auto"`) because the SVG-default orientation derives from the very
 * last sub-segment of the path, which can collapse in a smoothed corner and
 * snap the arrow to a default angle. We compute the geometric tangent from
 * the last two bend points directly – that's reliable in every case.
 */
export function ElkSplineEdge(props: EdgeProps) {
  const {
    id,
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    style,
    markerEnd,
    label,
    labelStyle,
    labelBgPadding,
    labelBgBorderRadius,
    labelBgStyle,
    data,
  } = props;

  const points = (data as ElkSplineEdgeData | undefined)?.elkPoints;

  if (!points || points.length < 2 || hasDrifted(points, sourceX, sourceY, targetX, targetY)) {
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
        <BaseEdge id={id} path={path} markerEnd={markerEnd} style={style} />
        {renderLabel({ label, labelX, labelY, labelStyle, labelBgPadding, labelBgBorderRadius, labelBgStyle })}
      </>
    );
  }

  const corrected = points.slice();
  corrected[0] = { x: sourceX, y: sourceY };
  corrected[corrected.length - 1] = { x: targetX, y: targetY };

  const path = roundedPolyline(corrected, 14);

  const last = corrected[corrected.length - 1];
  const beforeLast = corrected[corrected.length - 2];
  const angleRad = Math.atan2(last.y - beforeLast.y, last.x - beforeLast.x);
  const angleDeg = (angleRad * 180) / Math.PI;

  const strokeColor =
    (style as React.CSSProperties | undefined)?.stroke?.toString() ?? "var(--muted-foreground)";

  const mid = corrected[Math.floor(corrected.length / 2)];

  return (
    <>
      <BaseEdge id={id} path={path} style={style} />
      <g
        transform={`translate(${last.x} ${last.y}) rotate(${angleDeg})`}
        style={{ pointerEvents: "none" }}
      >
        <polygon
          points="-10,-5 0,0 -10,5"
          style={{ fill: strokeColor, stroke: strokeColor, strokeLinejoin: "round" }}
        />
      </g>
      {renderLabel({
        label,
        labelX: mid.x,
        labelY: mid.y,
        labelStyle,
        labelBgPadding,
        labelBgBorderRadius,
        labelBgStyle,
      })}
    </>
  );
}

function renderLabel({
  label,
  labelX,
  labelY,
  labelStyle,
  labelBgPadding,
  labelBgBorderRadius,
}: {
  label: EdgeProps["label"];
  labelX: number;
  labelY: number;
  labelStyle?: React.CSSProperties;
  labelBgPadding?: [number, number];
  labelBgBorderRadius?: number;
  labelBgStyle?: React.CSSProperties;
}) {
  if (label === undefined || label === null || label === "") return null;
  return (
    <EdgeLabelRenderer>
      <div
        style={{
          position: "absolute",
          transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
          padding: labelBgPadding
            ? `${labelBgPadding[1]}px ${labelBgPadding[0]}px`
            : "2px 4px",
          borderRadius: labelBgBorderRadius ?? 4,
          border: "1px solid var(--border)",
          background: "var(--card)",
          fontSize: 10,
          color: "var(--muted-foreground)",
          pointerEvents: "all",
          ...labelStyle,
        }}
        className="nodrag nopan"
      >
        {label}
      </div>
    </EdgeLabelRenderer>
  );
}

function hasDrifted(
  points: ElkPoint[],
  sourceX: number,
  sourceY: number,
  targetX: number,
  targetY: number,
): boolean {
  const first = points[0];
  const last = points[points.length - 1];
  const drift =
    Math.abs(first.x - sourceX) +
    Math.abs(first.y - sourceY) +
    Math.abs(last.x - targetX) +
    Math.abs(last.y - targetY);
  return drift > 12;
}

/**
 * Rounded polyline through the bend points: straight `L` segments joined by
 * a quadratic Bezier corner at each interior point. The final straight
 * segment guarantees a clean tangent at the target.
 */
function roundedPolyline(points: ElkPoint[], cornerRadius: number): string {
  if (points.length === 0) return "";
  if (points.length === 1) return `M ${points[0].x} ${points[0].y}`;
  if (points.length === 2) {
    return `M ${points[0].x} ${points[0].y} L ${points[1].x} ${points[1].y}`;
  }

  let d = `M ${points[0].x} ${points[0].y}`;
  for (let i = 1; i < points.length - 1; i++) {
    const prev = points[i - 1];
    const curr = points[i];
    const next = points[i + 1];

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
  d += ` L ${points[points.length - 1].x} ${points[points.length - 1].y}`;
  return d;
}
