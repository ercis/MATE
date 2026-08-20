import { Position } from "@xyflow/react";

import type { LayoutDirection } from "@/lib/stores/visualization-settings";

/**
 * Where to anchor the source/target handles based on the user's chosen
 * layout direction. Keeping these in sync with the ELK direction is what
 * makes edges enter/exit the right side of each box.
 */
export function handlePositions(direction: LayoutDirection): {
  source: Position;
  target: Position;
} {
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
