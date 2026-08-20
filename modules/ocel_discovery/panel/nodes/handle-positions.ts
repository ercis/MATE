import { Position } from "@xyflow/react";

export type Direction = "LR" | "RL" | "TB" | "BT";

/** Source/target handle sides for a given layout direction. */
export function handlePositions(direction: Direction): { source: Position; target: Position } {
  switch (direction) {
    case "LR":
      return { target: Position.Left, source: Position.Right };
    case "RL":
      return { target: Position.Right, source: Position.Left };
    case "TB":
      return { target: Position.Top, source: Position.Bottom };
    case "BT":
      return { target: Position.Bottom, source: Position.Top };
  }
}
