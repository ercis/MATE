"use client";

// Shared canvas chrome used by every canvas view – both the React Flow shell
// (`canvas-shell.tsx`) and the standalone bpmn-js viewers (discovery /
// conformance). Exposed to module bundles as a runtime external
// (`runtime-externals.json` + `module-runtime.ts`), so keep the surface small
// and dependency-light.

import { useCallback, useEffect, useRef, useState } from "react";
import { Maximize2, Minimize2 } from "lucide-react";

import { Button } from "@/components/ui/button";

// Cross-fade timings for entering/leaving pseudo-fullscreen.
const FS_ENTER_MS = 180;
const FS_EXIT_MS = 180;
// Inline props we overwrite while fullscreen (kebab-case for set/getProperty,
// which sidesteps CSSStyleDeclaration keys TS doesn't model, e.g. `inset`).
const FS_STYLE_PROPS = [
  "position",
  "inset",
  "margin",
  "width",
  "height",
  "z-index",
  "opacity",
  "transition",
] as const;

/**
 * Maximise an element to fill the platform viewport – a *pseudo*-fullscreen
 * overlay (`position: fixed; inset: 0`), NOT the browser Fullscreen API. Keeps
 * the browser chrome (tabs, URL bar) in place and just expands the canvas over
 * the app shell, so there's no OS-level fullscreen jump / black letterboxing.
 *
 * Enter fades the overlay in over the app. Exit fades a throwaway *clone* of the
 * fullscreen view out while the real element is restored into its card slot
 * underneath – one node can't be both the fading overlay and the revealed card,
 * so fading the element itself would flash the empty slot and pop the card back.
 * A hidden placeholder holds the slot in normal flow (no sibling reflow), and
 * `isFullscreen` flips to `false` as the card is restored so consumers re-fit
 * against the card size, not the viewport (the clone hides that reflow).
 * `prefers-reduced-motion` collapses both to an instant toggle. Esc exits.
 * SSR-guarded. Generic over the element type so a `useRef<HTMLDivElement>(null)`
 * passes without a cast.
 */
export function useFullscreen<T extends HTMLElement>(
  ref: React.RefObject<T | null>,
): { isFullscreen: boolean; toggle: () => void } {
  const [isFullscreen, setIsFullscreen] = useState(false);
  // Inline styles captured on enter; replayed to restore the element on exit.
  const savedRef = useRef<Record<string, string> | null>(null);
  // Flow-slot placeholder kept in the DOM while fullscreen (removed on exit).
  const placeholderRef = useRef<HTMLDivElement | null>(null);
  // Throwaway fullscreen clone faded out on exit (see EXIT below).
  const cloneRef = useRef<HTMLElement | null>(null);
  // Blocks re-entrant toggles (double-click / Esc) mid-animation.
  const busyRef = useRef(false);

  const toggle = useCallback(() => {
    if (typeof document === "undefined") return;
    const el = ref.current;
    if (!el || busyRef.current) return;

    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    const restore = () => {
      const s = savedRef.current;
      if (!s) return;
      for (const p of FS_STYLE_PROPS) el.style.setProperty(p, s[p]);
      savedRef.current = null;
    };

    // ---------- ENTER ----------
    if (!isFullscreen) {
      const card = el.getBoundingClientRect();

      const snap: Record<string, string> = {};
      for (const p of FS_STYLE_PROPS) snap[p] = el.style.getPropertyValue(p);
      savedRef.current = snap;

      // Placeholder holds the card's slot so nothing else reflows.
      const ph = document.createElement("div");
      ph.style.width = `${card.width}px`;
      ph.style.height = `${card.height}px`;
      ph.style.flex = "none";
      ph.style.visibility = "hidden";
      ph.setAttribute("aria-hidden", "true");
      el.parentNode?.insertBefore(ph, el);
      placeholderRef.current = ph;

      // Fill the viewport above the app shell.
      el.style.setProperty("position", "fixed");
      el.style.setProperty("inset", "0");
      el.style.setProperty("margin", "0");
      el.style.setProperty("width", "100%");
      el.style.setProperty("height", "100%");
      el.style.setProperty("z-index", "50");

      // Report fullscreen now: the element is already viewport-sized, so a
      // consumer re-fit (keyed on this flag) frames the graph at full size.
      setIsFullscreen(true);
      if (reduce) return;

      // Fade in over the app.
      busyRef.current = true;
      el.style.setProperty("transition", "none");
      el.style.setProperty("opacity", "0");
      void el.offsetHeight; // commit the start frame
      el.style.setProperty("transition", `opacity ${FS_ENTER_MS}ms ease-out`);
      el.style.setProperty("opacity", "1");
      const onEnd = (e: TransitionEvent) => {
        if (e.target !== el || e.propertyName !== "opacity") return;
        el.removeEventListener("transitionend", onEnd);
        el.style.setProperty("transition", "");
        el.style.setProperty("opacity", "");
        busyRef.current = false;
      };
      el.addEventListener("transitionend", onEnd);
      return;
    }

    // ---------- EXIT ----------
    // The fullscreen node and its card slot are the *same* element, so fading
    // the element out would just reveal the empty placeholder slot and then pop
    // the card back in. Instead, fade a throwaway clone of the current
    // fullscreen view out while restoring the real element into its slot
    // immediately underneath it (already re-fit, no hole, no pop).
    const ph = placeholderRef.current;
    const teardown = () => {
      ph?.parentNode?.removeChild(ph);
      placeholderRef.current = null;
      restore(); // element returns to the card box, in flow, where the slot was
      setIsFullscreen(false); // now consumers re-fit against the card size
    };

    if (reduce) {
      teardown();
      busyRef.current = false;
      return;
    }

    busyRef.current = true;
    // Snapshot the live fullscreen view (SVG/HTML subtrees clone visually).
    const clone = el.cloneNode(true) as HTMLElement;
    for (const [p, v] of [
      ["position", "fixed"],
      ["inset", "0"],
      ["margin", "0"],
      ["width", "100%"],
      ["height", "100%"],
      ["z-index", "50"],
      ["opacity", "1"],
      ["transition", "none"],
      ["pointer-events", "none"],
    ] as const) {
      clone.style.setProperty(p, v);
    }
    clone.setAttribute("aria-hidden", "true");
    document.body.appendChild(clone);
    cloneRef.current = clone;

    // Reveal the restored card beneath the still-opaque clone, then fade it out.
    teardown();
    void clone.offsetHeight;
    clone.style.setProperty("transition", `opacity ${FS_EXIT_MS}ms ease-in`);
    clone.style.setProperty("opacity", "0");

    let done = false;
    const settle = () => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      clone.removeEventListener("transitionend", onEnd);
      clone.parentNode?.removeChild(clone);
      cloneRef.current = null;
      busyRef.current = false;
    };
    const onEnd = (e: TransitionEvent) => {
      if (e.target !== clone || e.propertyName !== "opacity") return;
      settle();
    };
    clone.addEventListener("transitionend", onEnd);
    // Fallback if transitionend is dropped (tab hidden mid-anim, etc.).
    const timer = setTimeout(settle, FS_EXIT_MS + 120);
  }, [isFullscreen, ref]);

  // Esc exits while fullscreen.
  useEffect(() => {
    if (!isFullscreen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") toggle();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [isFullscreen, toggle]);

  // Drop a stray placeholder / exit-clone if the canvas unmounts mid-animation.
  useEffect(
    () => () => {
      placeholderRef.current?.parentNode?.removeChild(placeholderRef.current);
      cloneRef.current?.parentNode?.removeChild(cloneRef.current);
    },
    [],
  );

  return { isFullscreen, toggle };
}

/** Ghost icon button matching the canvas-shell toolbar, toggling fullscreen. */
export function CanvasFullscreenButton({
  isFullscreen,
  onToggle,
}: {
  isFullscreen: boolean;
  onToggle: () => void;
}) {
  const Icon = isFullscreen ? Minimize2 : Maximize2;
  return (
    <Button
      variant="ghost"
      size="icon"
      className="h-7 w-7 cursor-pointer"
      onClick={onToggle}
      aria-label={isFullscreen ? "Exit fullscreen" : "Enter fullscreen"}
      title={isFullscreen ? "Exit fullscreen" : "Fullscreen"}
    >
      <Icon className="h-3.5 w-3.5" />
    </Button>
  );
}

/**
 * Show-on-interaction visibility gate for canvas chrome (the minimap).
 *
 * `notifyActivity()` flips `visible` true and (re)arms a timer that flips it
 * back to false after `idleMs` of no further activity. Callers pump
 * `notifyActivity` from pan/zoom/drag/wheel/pointer signals; consumers fade the
 * element on `visible`.
 */
export function useCanvasIdleVisibility({ idleMs = 1200 }: { idleMs?: number } = {}): {
  visible: boolean;
  notifyActivity: () => void;
} {
  const [visible, setVisible] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const notifyActivity = useCallback(() => {
    setVisible(true);
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setVisible(false), idleMs);
  }, [idleMs]);

  useEffect(
    () => () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    },
    [],
  );

  return { visible, notifyActivity };
}

/** Minimal structural view of the diagram-js `canvas` service we need here. */
type BpmnCanvasLike = { zoom: (scale?: number | string, center?: string) => number };

/**
 * Fit a bpmn-js diagram into its viewport, centred on **both** axes with even
 * breathing room on every side.
 *
 * diagram-js's own `zoom("fit-viewport")` (a) pins the diagram top-left unless a
 * center reference is passed and (b) has no padding knob, so it fits edge-to-edge.
 * We fix both: `"auto"` centres the fit, then a second zoom shrinks around the
 * (now centred) viewport midpoint to inset the content. `margin` is the fraction
 * of the viewport left blank per side (0.05 ⇒ ~5% each side).
 */
export function fitBpmnViewport(canvas: BpmnCanvasLike, margin = 0.05): void {
  canvas.zoom("fit-viewport", "auto");
  canvas.zoom(canvas.zoom() * (1 - 2 * margin), "auto");
}

// --------------------------------------------------------------------------
// Smooth bpmn-js zoom
// --------------------------------------------------------------------------

/** diagram-js's `Canvas.viewbox()` reading. */
export interface BpmnViewbox {
  x: number;
  y: number;
  width: number;
  height: number;
  scale: number;
  /** Bounding box of the active layer's elements, in diagram coordinates. */
  inner: { x: number; y: number; width: number; height: number };
  /** The viewport, in CSS pixels. */
  outer: { width: number; height: number };
}

/**
 * The slice of the diagram-js `canvas` service `useSmoothBpmnZoom` drives.
 *
 * Declared with method syntax, not function properties: that keeps the check
 * bivariant, so the narrower inline `canvas` types the other bpmn viewers
 * already declare stay assignable to this one.
 */
export interface BpmnZoomCanvas {
  zoom(scale?: number | string, center?: string | { x: number; y: number }): number;
  viewbox(box?: { x: number; y: number; width: number; height: number }): BpmnViewbox;
}

/** diagram-js's own wheel-zoom limits (`ZoomScroll`'s RANGE), mirrored so the
 *  buttons and the wheel bottom out at the same scale. */
const ZOOM_MIN = 0.2;
const ZOOM_MAX = 4;
/** One button click / one Fit. */
const ZOOM_TWEEN_MS = 220;
/** Log-space scale change per pixel of pinch delta. Matched to diagram-js's
 *  effective pinch rate (one ~16% step per ~6.7px of accumulated delta), so
 *  the speed is unchanged – what this drops is the quantisation. */
const WHEEL_ZOOM_RATE = 0.022;
/** Per-event ceiling. A mouse notch reports `deltaY` ≈ 100, which at the rate
 *  above would be a ~9x leap; diagram-js dodges that by ignoring the magnitude
 *  entirely and always moving exactly one step. */
const WHEEL_ZOOM_MAX_STEP = 1.18;

/**
 * Clamp to diagram-js's zoom range, widened to always admit `current`.
 *
 * A fit on a very large diagram legitimately lands *below* `ZOOM_MIN`, and a
 * hard clamp there would make a zoom-out step come back at 0.2 – i.e. zoom in.
 * Widening keeps every step monotonic in the direction the user asked for.
 */
const clampZoom = (scale: number, current: number) =>
  Math.min(Math.max(ZOOM_MAX, current), Math.max(Math.min(ZOOM_MIN, current), scale));
const easeOutCubic = (p: number) => 1 - (1 - p) ** 3;
const prefersReducedMotion = () =>
  typeof window !== "undefined" &&
  (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false);
/** Wheel delta normalised to CSS pixels – Firefox reports lines / pages. */
const wheelPixels = (e: WheelEvent) =>
  e.deltaMode === 1 ? e.deltaY * 16 : e.deltaMode === 2 ? e.deltaY * 400 : e.deltaY;

export interface BpmnZoomControls {
  zoomIn: () => void;
  zoomOut: () => void;
  /** Animated fit-to-viewport – wire this to the cluster's Fit button. */
  fit: () => void;
  /** Instant fit, for re-frames the user didn't ask for (import, resize). */
  fitNow: () => void;
}

/**
 * Smooth zooming for a standalone bpmn-js viewer.
 *
 * diagram-js's zoom is chunky by design: `ZoomScroll._zoom` *snaps* the scale
 * onto a fixed logarithmic grid (`round(level / step) * step`) before stepping
 * once, so a continuous trackpad pinch lands as a run of ~16% jumps and the
 * toolbar buttons teleport. This replaces both paths:
 *
 * - **Buttons / Fit** tween over `ZOOM_TWEEN_MS`, interpolated in *log* space so
 *   every frame is the same ratio step (interpolating the scale linearly reads
 *   as a slowdown). Repeated clicks compound onto the pending target rather than
 *   re-aiming from wherever the tween happens to be.
 * - **Pinch / ctrl+wheel** zoom continuously at the cursor, unsnapped, capped
 *   per event so a coarse mouse notch can't leap.
 *
 * Plain wheel is left alone – that's diagram-js panning the canvas – as is the
 * minimap, which handles its own wheel events. `prefers-reduced-motion` collapses
 * the tweens to an instant jump; the wheel path is direct manipulation, so it
 * stays continuous either way.
 *
 * `containerRef` must be the element handed to the viewer as its `container`:
 * diagram-js binds its own wheel listener on a `.djs-container` child of it, so
 * a capture-phase listener here is what gets to pre-empt the stepped zoom.
 */
export function useSmoothBpmnZoom(
  getCanvas: () => BpmnZoomCanvas | null,
  containerRef: React.RefObject<HTMLElement | null>,
  { margin = 0.05, onActivity }: { margin?: number; onActivity?: () => void } = {},
): BpmnZoomControls {
  // Read through refs so the wheel effect can stay mounted for the component's
  // lifetime instead of re-binding whenever a caller's closure changes.
  const getCanvasRef = useRef(getCanvas);
  getCanvasRef.current = getCanvas;
  const onActivityRef = useRef(onActivity);
  onActivityRef.current = onActivity;
  const marginRef = useRef(margin);
  marginRef.current = margin;

  const rafRef = useRef<number | null>(null);
  // Where the in-flight tween is heading, so a burst of button clicks compounds
  // (1.3x, 1.69x, …) instead of each click re-aiming from the current frame.
  const targetRef = useRef<number | null>(null);

  const stop = useCallback(() => {
    if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    rafRef.current = null;
    targetRef.current = null;
  }, []);
  useEffect(() => stop, [stop]);

  const zoomBy = useCallback((factor: number) => {
    const canvas = getCanvasRef.current();
    if (!canvas) return;
    const base = targetRef.current ?? canvas.zoom();
    if (!Number.isFinite(base) || base <= 0) return;
    const to = clampZoom(base * factor, base);
    const from = canvas.zoom();
    if (!Number.isFinite(from) || from <= 0) return;

    if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    rafRef.current = null;
    targetRef.current = to;
    onActivityRef.current?.();

    if (prefersReducedMotion()) {
      canvas.zoom(to, "auto");
      targetRef.current = null;
      return;
    }
    const ratio = to / from;
    // Already there (clamped at a limit, or a no-op click).
    if (Math.abs(Math.log(ratio)) < 1e-3) return;

    const t0 = performance.now();
    const frame = (now: number) => {
      // Re-read: the viewer can be torn down mid-tween.
      if (getCanvasRef.current() !== canvas) {
        rafRef.current = null;
        targetRef.current = null;
        return;
      }
      const p = Math.min(1, (now - t0) / ZOOM_TWEEN_MS);
      canvas.zoom(from * ratio ** easeOutCubic(p), "auto");
      if (p < 1) {
        rafRef.current = requestAnimationFrame(frame);
      } else {
        rafRef.current = null;
        targetRef.current = null;
      }
    };
    rafRef.current = requestAnimationFrame(frame);
  }, []);

  const fitNow = useCallback(() => {
    const canvas = getCanvasRef.current();
    if (canvas) fitBpmnViewport(canvas, marginRef.current);
  }, []);

  const fit = useCallback(() => {
    const canvas = getCanvasRef.current();
    if (!canvas) return;
    if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    rafRef.current = null;
    targetRef.current = null;
    onActivityRef.current?.();

    const m = marginRef.current;
    const vb = canvas.viewbox();
    const { inner, outer } = vb;
    // Nothing measurable yet – an empty diagram, no element bbox, or a viewport
    // with no size. Nothing to interpolate towards, so just land on it.
    if (
      !(inner.width > 0) ||
      !(inner.height > 0) ||
      !(outer.width > 0) ||
      !(outer.height > 0) ||
      !(vb.scale > 0) ||
      prefersReducedMotion()
    ) {
      fitBpmnViewport(canvas, m);
      return;
    }
    // The framing `fitBpmnViewport` lands on, computed rather than applied so we
    // can animate into it: diagram-js's fit scale, inset by `margin` per side,
    // centred on the element bbox.
    const to = {
      cx: inner.x + inner.width / 2,
      cy: inner.y + inner.height / 2,
      s: Math.min(1, outer.width / inner.width, outer.height / inner.height) * (1 - 2 * m),
    };
    const from = { cx: vb.x + vb.width / 2, cy: vb.y + vb.height / 2, s: vb.scale };
    const ratio = to.s / from.s;
    const t0 = performance.now();
    const frame = (now: number) => {
      if (getCanvasRef.current() !== canvas) {
        rafRef.current = null;
        return;
      }
      const p = Math.min(1, (now - t0) / ZOOM_TWEEN_MS);
      const e = easeOutCubic(p);
      // Scale in log space, the centre linearly – the viewbox setter derives the
      // scale from width/height, so both ratios have to resolve to `s`.
      const s = from.s * ratio ** e;
      const width = outer.width / s;
      const height = outer.height / s;
      canvas.viewbox({
        x: from.cx + (to.cx - from.cx) * e - width / 2,
        y: from.cy + (to.cy - from.cy) * e - height / 2,
        width,
        height,
      });
      rafRef.current = p < 1 ? requestAnimationFrame(frame) : null;
    };
    rafRef.current = requestAnimationFrame(frame);
  }, []);

  // Continuous cursor-anchored pinch / ctrl+wheel zoom. Native and non-passive:
  // React registers wheel listeners passively, so `preventDefault` from an
  // `onWheel` prop is not honoured (same reason the dashboard canvas binds its
  // own). Capture phase so `stopPropagation` keeps diagram-js's stepped zoom –
  // bound on the `.djs-container` child – from firing on top of this.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      // Browsers report a trackpad pinch as ctrl+wheel. Plain wheel is a pan
      // and belongs to diagram-js.
      if (!e.ctrlKey && !e.metaKey) return;
      // The minimap does its own thing with the wheel.
      if ((e.target as Element | null)?.closest?.(".djs-minimap")) return;
      const canvas = getCanvasRef.current();
      if (!canvas) return;
      e.preventDefault();
      e.stopPropagation();

      const current = canvas.zoom();
      if (!Number.isFinite(current) || current <= 0) return;
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
      targetRef.current = null;

      const step = Math.min(
        WHEEL_ZOOM_MAX_STEP,
        Math.max(1 / WHEEL_ZOOM_MAX_STEP, Math.exp(-wheelPixels(e) * WHEEL_ZOOM_RATE)),
      );
      const rect = el.getBoundingClientRect();
      canvas.zoom(clampZoom(current * step, current), {
        x: e.clientX - rect.left,
        y: e.clientY - rect.top,
      });
      onActivityRef.current?.();
    };
    el.addEventListener("wheel", onWheel, { passive: false, capture: true });
    return () => el.removeEventListener("wheel", onWheel, { capture: true });
  }, [containerRef]);

  const zoomIn = useCallback(() => zoomBy(1.3), [zoomBy]);
  const zoomOut = useCallback(() => zoomBy(1 / 1.3), [zoomBy]);

  return { zoomIn, zoomOut, fit, fitNow };
}
