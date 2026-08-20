import type { LayeredOptions } from "./layered";
import type {
  EdgeRouting,
  LayoutDirection,
} from "@/lib/stores/visualization-settings";

export function mapDirection(d: LayoutDirection): NonNullable<LayeredOptions["direction"]> {
  switch (d) {
    case "LR":
      return "RIGHT";
    case "TB":
      return "DOWN";
    case "RL":
      return "LEFT";
    case "BT":
      return "UP";
  }
}

export function mapEdgeRouting(r: EdgeRouting): NonNullable<LayeredOptions["edgeRouting"]> {
  switch (r) {
    case "orthogonal":
      return "ORTHOGONAL";
    case "spline":
      return "SPLINES";
    case "straight":
      return "POLYLINE";
  }
}

/** xyflow edge "type" – visual curve style. */
export function mapEdgeType(r: EdgeRouting): "smoothstep" | "bezier" | "straight" | "step" {
  switch (r) {
    case "orthogonal":
      return "smoothstep";
    case "spline":
      return "bezier";
    case "straight":
      return "straight";
  }
}

export function truncate(label: string, max: number): string {
  if (label.length <= max) return label;
  return label.slice(0, Math.max(0, max - 1)) + "…";
}
