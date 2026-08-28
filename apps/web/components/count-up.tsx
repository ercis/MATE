"use client";

import { useEffect, useRef, useState } from "react";

import { formatNumber } from "@/lib/format";

/**
 * Animated integer: eases from the last shown value (0 on first mount) to
 * `value` over ~600ms via rAF. JS-driven, so the global CSS reduced-motion
 * kill-switch doesn't apply – it checks `prefers-reduced-motion` itself and
 * renders the plain number instead. Null/undefined render as formatNumber's
 * "–" placeholder.
 */
export function CountUp({ value }: { value: number | null | undefined }) {
  const target = typeof value === "number" ? value : null;
  const [display, setDisplay] = useState(0);
  const shownRef = useRef(0);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    if (target === null) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      shownRef.current = target;
      setDisplay(target);
      return;
    }
    const from = shownRef.current;
    if (from === target) return;
    const start = performance.now();
    const duration = 600;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      const current = Math.round(from + (target - from) * eased);
      shownRef.current = current;
      setDisplay(current);
      if (t < 1) rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    };
  }, [target]);

  if (target === null) return <>{formatNumber(value)}</>;
  return <>{formatNumber(display)}</>;
}
