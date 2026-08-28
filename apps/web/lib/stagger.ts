import type { CSSProperties } from "react";

/**
 * Inline style for staggered entrance animations. Pair with
 * `animate-in … fill-mode-both` – fill-mode is mandatory, otherwise the
 * element flashes fully visible during its delay. The delay caps at `max`
 * so long lists finish their cascade quickly instead of crawling.
 */
export function stagger(index: number, step = 40, max = 400): CSSProperties {
  return { animationDelay: `${Math.min(index * step, max)}ms` };
}
