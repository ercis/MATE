"use client";

import type { ReactNode } from "react";
import { ArrowDown, ArrowUp, Minus } from "lucide-react";

import { cn } from "@/lib/cn";
import { InfoHint } from "@/components/dashboards/kit/info-hint";

/**
 * One headline figure.
 *
 * The union of what every module's private `_kit.tsx` had grown separately —
 * discovery's had no explanation affordance, performance's did, agentsimulator
 * had a third variant with its own delta rendering. One tile now, so a board
 * of cards from three modules reads as one product.
 *
 * A dashboard card can hold several of these: declare them under `kpis:` in the
 * manifest and the user can choose which ones a given placement shows.
 */
export function KpiTile({
  label,
  value,
  hint,
  info,
  delta,
  deltaLabel,
  trend = "neutral",
  onClick,
  className,
}: {
  label: string;
  /** Pre-formatted. Use `formatNumber`/`formatDuration` from `@/lib/format` so
   * every card renders the same kind of quantity the same way. */
  value: string;
  /** Small qualifier under the value ("median, 1 240 cases"). */
  hint?: string;
  /** Plain-language explanation behind a ⓘ. Worth writing for anything whose
   * label alone is ambiguous. */
  info?: ReactNode;
  /** Pre-formatted change, e.g. "+12%". */
  delta?: string;
  /** What the delta is measured against ("vs. previous 30 days"). Without this
   * a delta is unreadable — a bare "+12%" answers nothing. */
  deltaLabel?: string;
  /** Whether the delta is good, bad, or neither. NOT the direction of the
   * arrow: a falling cycle time is `good`. Colour never carries this alone —
   * the arrow and the label do. */
  trend?: "good" | "bad" | "neutral";
  /** Makes the tile a drill target. Wire it to the widget's `onDrill`. */
  onClick?: () => void;
  className?: string;
}) {
  const Arrow = trend === "good" ? ArrowUp : trend === "bad" ? ArrowDown : Minus;
  const interactive = typeof onClick === "function";

  const body = (
    <>
      <div className="flex items-center gap-1 text-muted-foreground">
        <span className="truncate text-[11px] uppercase tracking-wide">{label}</span>
        {info && <InfoHint label={`What does ${label} mean?`}>{info}</InfoHint>}
      </div>
      {/* Proportional figures, not tabular: equal-width digits make a large
          standalone number look loose. `tabular-nums` belongs in columns. */}
      <div className="mt-0.5 truncate text-lg font-semibold tracking-tight">{value}</div>
      {delta && (
        <div
          className={cn(
            "mt-0.5 flex items-center gap-1 text-[11px]",
            trend === "good" && "text-[var(--ff-status-good)]",
            trend === "bad" && "text-[var(--ff-status-critical)]",
            trend === "neutral" && "text-muted-foreground",
          )}
        >
          <Arrow className="h-3 w-3 shrink-0" aria-hidden="true" />
          <span className="truncate">
            {delta}
            {deltaLabel && <span className="text-muted-foreground"> {deltaLabel}</span>}
          </span>
        </div>
      )}
      {hint && <div className="mt-0.5 truncate text-[11px] text-muted-foreground">{hint}</div>}
    </>
  );

  const shell = cn(
    "min-w-0 rounded-md border border-border/60 bg-muted/20 px-3 py-2 text-left",
    interactive &&
      "cursor-pointer transition-colors hover:border-border hover:bg-muted/40 " +
        "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
    className,
  );

  if (!interactive) return <div className={shell}>{body}</div>;
  return (
    <button type="button" onClick={onClick} className={shell}>
      {body}
    </button>
  );
}

/**
 * Responsive grid of `KpiTile`s.
 *
 * Tracks are sized with an inline `repeat(auto-fit, minmax(...))` rather than a
 * `grid-cols-N` class ON PURPOSE: module panels are bundled separately from the
 * web app, so a Tailwind class used only inside `modules/**` is never emitted
 * and silently does nothing. Inline styles always survive the bundler.
 *
 * Because tracks reflow, the same card works at three tiles wide or one — which
 * is what lets a placement show a subset of a multi-KPI widget.
 */
export function KpiGrid({
  children,
  minTileWidth = 150,
  className,
}: {
  children: ReactNode;
  /** Narrowest a tile may get before the grid drops to fewer columns. */
  minTileWidth?: number;
  className?: string;
}) {
  return (
    <div
      className={cn("grid gap-2", className)}
      style={{ gridTemplateColumns: `repeat(auto-fit, minmax(${minTileWidth}px, 1fr))` }}
    >
      {children}
    </div>
  );
}
