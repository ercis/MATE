"use client";

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { usePathname } from "next/navigation";
import { ArrowLeft, ArrowRight, Check, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";
import { useProgressRouter } from "@/lib/use-progress-router";
import { useEventLogs } from "@/lib/queries";
import { useOnboardingState, useUpdateOnboarding } from "@/lib/onboarding-queries";
import { useTour } from "@/lib/stores/tour";
import { buildTourSteps, type TourStep } from "@/lib/tour/steps";

type Rect = { top: number; left: number; width: number; height: number };

const SPOTLIGHT_PAD = 8;
// Generous: a step can target an element that only mounts after a route change,
// so we keep polling across the navigation before falling back to a centered card.
const FIND_TIMEOUT_MS = 6000;
const TOOLTIP_W = 340;
const VIEWPORT_MARGIN = 12;
const TARGET_GAP = 14;
const DIM = "rgba(2, 6, 23, 0.55)";

/**
 * The log the tour walks through.
 *
 * The wizard's own upload wins outright, even though it is still `importing` -
 * a brand-new user has no `ready` log, and skipping their freshly imported data
 * to spotlight nothing was the whole reason the first run felt disjointed. The
 * detail page and the list row both explain the in-flight state, so an importing
 * log is a perfectly good thing to look at.
 *
 * Otherwise prefer a `ready` log (fully rendered output), then any log that
 * isn't `failed`. `null` → the log-bound steps render as centered cards.
 */
function resolveTourLogId(
  storeLogId: string | null,
  logs: { id: string; status: string }[] | undefined,
): string | null {
  if (storeLogId) return storeLogId;
  if (!logs || logs.length === 0) return null;
  return (
    logs.find((l) => l.status === "ready")?.id ??
    logs.find((l) => l.status !== "failed")?.id ??
    null
  );
}

function near(a: Rect | null, b: Rect | null): boolean {
  if (!a || !b) return a === b;
  return (
    Math.abs(a.top - b.top) < 0.5 &&
    Math.abs(a.left - b.left) < 0.5 &&
    Math.abs(a.width - b.width) < 0.5 &&
    Math.abs(a.height - b.height) < 0.5
  );
}

/**
 * Top-left for the tooltip card, kept fully on-screen: prefer the requested
 * side, flip to the opposite side when the card wouldn't fit, then hard-clamp
 * the whole box inside the viewport. Takes the *measured* card size (`size`)
 * and positions transform-free, so the clamp accounts for the real card and no
 * edge can leave the screen (the old transform-based version clamped the
 * pre-transform anchor, letting tall cards overflow near an edge).
 */
function placeTooltip(
  rect: Rect | null,
  placement: TourStep["placement"],
  size: { w: number; h: number },
): { top: number; left: number } {
  const vw = typeof window === "undefined" ? 1024 : window.innerWidth;
  const vh = typeof window === "undefined" ? 768 : window.innerHeight;
  const { w, h } = size;
  const m = VIEWPORT_MARGIN;
  const gap = TARGET_GAP;
  const clampLeft = (l: number) => Math.max(m, Math.min(l, vw - w - m));
  const clampTop = (t: number) => Math.max(m, Math.min(t, vh - h - m));

  if (!rect) {
    return { top: clampTop((vh - h) / 2), left: clampLeft((vw - w) / 2) };
  }

  const place = placement ?? "bottom";
  const fitsBelow = rect.top + rect.height + gap + h <= vh - m;
  const fitsAbove = rect.top - gap - h >= m;
  const fitsRight = rect.left + rect.width + gap + w <= vw - m;
  const fitsLeft = rect.left - gap - w >= m;

  let top: number;
  let left: number;

  if (place === "left" || place === "right") {
    const useRight = place === "right" ? fitsRight || !fitsLeft : !(fitsLeft || !fitsRight);
    left = useRight ? rect.left + rect.width + gap : rect.left - gap - w;
    top = rect.top + rect.height / 2 - h / 2;
  } else {
    const useBelow = place === "bottom" ? fitsBelow || !fitsAbove : !(fitsAbove || !fitsBelow);
    top = useBelow ? rect.top + rect.height + gap : rect.top - gap - h;
    left = rect.left + rect.width / 2 - w / 2;
  }

  return { top: clampTop(top), left: clampLeft(left) };
}

export function TourOverlay() {
  const active = useTour((s) => s.active);
  // Keep all the data hooks out of the tree until the tour actually runs.
  if (!active) return null;
  return <TourRunner />;
}

function TourRunner() {
  const stepIndex = useTour((s) => s.stepIndex);
  const next = useTour((s) => s.next);
  const prev = useTour((s) => s.prev);
  const stop = useTour((s) => s.stop);
  const auto = useTour((s) => s.auto);
  const tourLogId = useTour((s) => s.logId);

  const router = useProgressRouter();
  const pathname = usePathname();
  const { data: logs } = useEventLogs();
  const onboarding = useOnboardingState();
  const updateOnboarding = useUpdateOnboarding();

  const logId = useMemo(() => resolveTourLogId(tourLogId, logs), [tourLogId, logs]);
  const steps = useMemo(() => buildTourSteps(logId, { auto }), [logId, auto]);

  const step: TourStep | undefined = steps[stepIndex];
  const isLast = stepIndex >= steps.length - 1;

  const [rect, setRect] = useState<Rect | null>(null);
  const [found, setFound] = useState(false);
  const [mounted, setMounted] = useState(false);
  const rectRef = useRef<Rect | null>(null);
  const scrolledForStep = useRef<number>(-1);
  const tipRef = useRef<HTMLDivElement>(null);
  const [tipSize, setTipSize] = useState({ w: TOOLTIP_W, h: 0 });
  const [, bumpResize] = useState(0);

  useEffect(() => setMounted(true), []);

  // Measure the rendered card (offset size → ignores the entrance transform) and
  // reposition when it changes; the body text length varies per step so the card
  // height does too. Guarded so the setState doesn't loop.
  useLayoutEffect(() => {
    const el = tipRef.current;
    if (!el) return;
    const w = el.offsetWidth;
    const h = el.offsetHeight;
    setTipSize((prev) =>
      Math.abs(prev.w - w) < 0.5 && Math.abs(prev.h - h) < 0.5 ? prev : { w, h },
    );
  });

  // Centered steps (welcome/done) have no rAF tracking, so re-render on resize to
  // recompute their position against the new viewport.
  useEffect(() => {
    const onResize = () => bumpResize((n) => n + 1);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const finish = useCallback(() => {
    // Persist completion (idempotent) and tear down. A partial patch – the API
    // merges it, so `completed`/`experience_level` are untouched.
    if (!onboarding.data?.tour_completed) updateOnboarding.mutate({ tour_completed: true });
    stop();
  }, [onboarding.data?.tour_completed, updateOnboarding, stop]);

  // Safety net: if the step list shrinks under us (e.g. the demo log is deleted
  // mid-tour) and the index runs past the end, end the tour cleanly rather than
  // leaving an active-but-blank overlay.
  useEffect(() => {
    if (stepIndex >= steps.length) finish();
  }, [stepIndex, steps.length, finish]);

  // Drive navigation: ensure we're on the step's route before resolving its
  // target. Compares path *and* query, because `/processes/{id}?tab=events` has
  // the same pathname as the overview but mounts none of the overview's anchors
  // - a user parked on another tab would otherwise sit there while the step
  // burned its find timeout into a centered fallback. `/processes/{id}` is the
  // canonical overview URL (the page deletes `?tab=` for it), so this is exact.
  // Read `window.location.search` rather than `useSearchParams()` to avoid
  // forcing a Suspense boundary around this layout-level overlay; the tour
  // blocks clicks, so the query can only change at step entry anyway.
  useEffect(() => {
    if (!step?.route) return;
    const search = typeof window === "undefined" ? "" : window.location.search;
    if (pathname + search !== step.route) router.push(step.route);
  }, [step?.route, pathname, router]);

  // Locate + track the target. Polls (rAF) so a target that mounts after a route
  // change is still picked up; updates only on real movement to avoid churn;
  // falls back to a centered card if it never appears. Resets on step + route
  // change (so navigation latency doesn't burn the find timeout early).
  useEffect(() => {
    rectRef.current = null;
    setRect(null);
    setFound(false);
    if (!step?.selector) return;

    let raf = 0;
    let stopped = false;
    const startedAt = performance.now();
    const selector = step.selector;
    const myStep = stepIndex;

    const tick = () => {
      if (stopped) return;
      const el = document.querySelector(selector) as HTMLElement | null;
      if (el) {
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.height > 0) {
          const measured: Rect = { top: r.top, left: r.left, width: r.width, height: r.height };
          if (!near(rectRef.current, measured)) {
            rectRef.current = measured;
            setRect(measured);
          }
          setFound(true);
          // Bring it into view once per step.
          if (scrolledForStep.current !== myStep) {
            scrolledForStep.current = myStep;
            const inView = r.top >= 0 && r.bottom <= window.innerHeight;
            if (!inView) el.scrollIntoView({ block: "center", behavior: "smooth" });
          }
        }
      } else if (performance.now() - startedAt > FIND_TIMEOUT_MS) {
        setFound(false); // give up → centered fallback
        return;
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      stopped = true;
      cancelAnimationFrame(raf);
    };
  }, [step?.selector, stepIndex, pathname]);

  // Keyboard: Esc skips, arrows navigate.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        finish();
      } else if (e.key === "ArrowRight") {
        if (isLast) finish();
        else next();
      } else if (e.key === "ArrowLeft") {
        if (stepIndex > 0) prev();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isLast, stepIndex, next, prev, finish]);

  if (!mounted || !step || typeof document === "undefined") return null;

  const hasSpotlight = found && rect !== null;
  const pos = placeTooltip(hasSpotlight ? rect : null, step.placement, tipSize);

  return createPortal(
    <div aria-live="polite" role="dialog" aria-label="Product tour">
      {/* Click-blocker. Carries the dim itself only when there's no spotlight
          (centered steps); otherwise the spotlight's box-shadow draws the dim. */}
      <div
        className="fixed inset-0 z-[60]"
        style={hasSpotlight ? undefined : { background: DIM }}
        onClick={(e) => e.stopPropagation()}
      />

      {/* Spotlight: a transparent rounded rect whose huge box-shadow dims the
          rest of the page, leaving the target lit and ringed. */}
      {hasSpotlight && rect && (
        <div
          className="pointer-events-none fixed z-[61] rounded-lg"
          style={{
            top: rect.top - SPOTLIGHT_PAD,
            left: rect.left - SPOTLIGHT_PAD,
            width: rect.width + SPOTLIGHT_PAD * 2,
            height: rect.height + SPOTLIGHT_PAD * 2,
            boxShadow: `0 0 0 9999px ${DIM}, 0 0 0 2px var(--primary)`,
          }}
        />
      )}

      {/* Tooltip card */}
      <div
        ref={tipRef}
        className="fixed z-[62] animate-in fade-in zoom-in-95 rounded-xl border border-border bg-popover text-popover-foreground shadow-2xl duration-200"
        style={{ top: pos.top, left: pos.left, width: TOOLTIP_W }}
      >
        <div className="flex items-center justify-between gap-3 px-4 pt-3.5">
          <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            Step {stepIndex + 1} of {steps.length}
          </span>
          <button
            type="button"
            onClick={finish}
            aria-label="End tour"
            className="cursor-pointer rounded-md p-1 text-muted-foreground/70 hover:bg-muted hover:text-foreground"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>

        <div className="px-4 pt-2">
          <h3 className="text-sm font-semibold leading-tight">{step.title}</h3>
          <p className="mt-1.5 text-sm leading-snug text-muted-foreground">{step.body}</p>
        </div>

        <div className="mt-3 flex items-center gap-1 px-4">
          {steps.map((s, i) => (
            <span
              key={s.id}
              className={cn(
                "h-1 rounded-full transition-all",
                i === stepIndex ? "w-5 bg-primary" : "w-1.5 bg-muted",
                i < stepIndex && "bg-primary/40",
              )}
            />
          ))}
        </div>

        <div className="mt-3 flex items-center justify-between gap-2 border-t border-border p-3">
          <Button
            variant="ghost"
            size="sm"
            onClick={finish}
            className="cursor-pointer text-muted-foreground"
          >
            Skip tour
          </Button>
          <div className="flex items-center gap-2">
            {stepIndex > 0 && (
              <Button
                variant="outline"
                size="sm"
                onClick={prev}
                className="cursor-pointer gap-1.5"
              >
                <ArrowLeft className="h-3.5 w-3.5" />
                Back
              </Button>
            )}
            <Button
              size="sm"
              onClick={isLast ? finish : next}
              className="cursor-pointer gap-1.5"
            >
              {isLast ? (
                <>
                  Done
                  <Check className="h-3.5 w-3.5" />
                </>
              ) : (
                <>
                  Next
                  <ArrowRight className="h-3.5 w-3.5" />
                </>
              )}
            </Button>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}
