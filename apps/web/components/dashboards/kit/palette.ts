/**
 * The chart palette every dashboard card draws from.
 *
 * Before this existed each module picked its own hex (`agentsimulator` had
 * `{real: "#6366f1", sim: "#f59e0b"}` inline), so a board of cards from three
 * modules read as three unrelated products and nothing was checked for
 * colourblind safety. These are CSS-variable-backed roles instead: they follow
 * the theme, and the values were validated rather than chosen by eye.
 *
 * Tokens live in `app/globals.css`; see the comment there for the measured
 * separation figures and how to re-validate if you change them.
 *
 * ── The rules ────────────────────────────────────────────────────────────────
 *
 * 1. Pick by JOB, not by looks:
 *      identity  -> seriesColor()      (distinct things: modules, activities)
 *      magnitude -> sequentialScale()  (more is darker, one hue)
 *      polarity  -> divergingScale()   (above/below a baseline, neutral middle)
 *      state     -> statusColor()      (good/warning/serious/critical ONLY)
 * 2. Assign series slots in fixed order and never cycle. Colour follows the
 *    entity, not its rank — filtering a series must not repaint the survivors,
 *    or a reader who learned "Approve is blue" is now being misled.
 * 3. Past 8 series, fold the tail into "Other" or facet. A generated 9th hue is
 *    indistinguishable from an existing one under CVD.
 * 4. Never use a status colour for a series, or a series colour for status.
 * 5. Text wears text tokens, never the series colour — a coloured mark beside
 *    a label carries the identity.
 */

/** How many distinct series the palette can encode. Hard ceiling. */
export const SERIES_SLOTS = 8;

/**
 * Colour for series `index`, assigned in fixed order.
 *
 * Pass the entity's *stable* position (its index in a sorted-by-name list, or a
 * lookup keyed by id) — NOT its rank in the current view, or the chart will
 * repaint itself whenever the data reorders.
 *
 * Beyond `SERIES_SLOTS` this wraps, which is a bug in the caller: two series
 * become the same colour. Fold the tail into "Other" instead.
 *
 * LIGHT-MODE CAVEAT: slots 3, 4 and 5 (aqua, yellow, magenta) sit below 3:1
 * against the light surface. A chart that reaches them must carry visible
 * direct labels or a table view — colour alone is not readable there.
 */
export function seriesColor(index: number): string {
  const slot = ((Math.trunc(index) % SERIES_SLOTS) + SERIES_SLOTS) % SERIES_SLOTS;
  return `var(--ff-series-${slot + 1})`;
}

/** Every series colour in slot order — for a legend, or a chart that wants the
 * whole ramp up front. */
export const SERIES_COLORS: readonly string[] = Array.from({ length: SERIES_SLOTS }, (_, i) =>
  seriesColor(i),
);

/** The five sequential steps, lightest (near zero) to darkest (the maximum).
 * One hue: a rainbow ramp cannot be read as an ordered magnitude. */
export const SEQUENTIAL_STEPS: readonly string[] = [
  "var(--ff-seq-100)",
  "var(--ff-seq-250)",
  "var(--ff-seq-400)",
  "var(--ff-seq-550)",
  "var(--ff-seq-700)",
];

/**
 * Sequential colour for a magnitude, mapped onto the ramp.
 *
 * `t` is the value's position in the data's range, 0..1 — normalise before
 * calling. Out-of-range values clamp rather than fall off the ramp.
 *
 * For DISCRETE ordered marks (funnel stages, tiers) start at step 250 instead
 * of 100 — the lightest step is allowed to recede into the surface only when it
 * genuinely means "near zero" on a continuous scale.
 */
export function sequentialScale(t: number, { ordinal = false }: { ordinal?: boolean } = {}): string {
  const steps = ordinal ? SEQUENTIAL_STEPS.slice(1) : SEQUENTIAL_STEPS;
  if (!Number.isFinite(t)) return steps[0];
  const clamped = Math.min(1, Math.max(0, t));
  return steps[Math.min(steps.length - 1, Math.round(clamped * (steps.length - 1)))];
}

/**
 * Diverging colour for a signed deviation from a baseline.
 *
 * `t` is -1..1, where 0 is the baseline. The midpoint is NEUTRAL GREY on
 * purpose: it has to read as "nothing". A third hue there turns "no deviation"
 * into a category of its own.
 */
export function divergingScale(t: number): string {
  if (!Number.isFinite(t) || t === 0) return "var(--ff-div-mid)";
  return t < 0 ? "var(--ff-div-low)" : "var(--ff-div-high)";
}

/** The reserved status roles. Not series colours — never use one to mean
 * "the fourth thing". */
export type StatusRole = "good" | "warning" | "serious" | "critical";

/**
 * Colour for a status.
 *
 * Always ships with an icon AND a label. Two of these are deliberately below
 * 3:1 on the light surface, and more importantly a colour alone cannot be read
 * by everyone — the icon and the word are what carry the meaning.
 */
export function statusColor(role: StatusRole): string {
  return `var(--ff-status-${role})`;
}

/** Recessive chart chrome. Gridlines and axes are solid hairlines one shade off
 * the surface — never dashed, which reads as "threshold" when it is just a
 * grid. */
export const CHART_CHROME = {
  grid: "var(--ff-grid-line)",
  axis: "var(--ff-axis-line)",
  /** De-emphasis grey for the "everything else" series in an emphasis chart —
   * one series in colour, the rest in this. */
  muted: "var(--muted-foreground)",
} as const;
