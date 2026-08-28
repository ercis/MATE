/**
 * Scientific auto-thresholding for the DFG sliders: knee ("elbow") detection
 * on the cumulative frequency-coverage curve, in the spirit of Kneedle
 * (Satopaa et al. 2011, "Finding a 'Kneedle' in a Haystack"), simplified for
 * the monotone-concave curves DFG frequency distributions produce.
 *
 * Sort items (activities or edges) by frequency descending, plot
 * x_i = i/n (share of items kept) against y_i = cum_i/total (share of
 * observations covered). The knee is the point of maximum vertical distance
 * to the y = x chord — past it, each extra item buys less coverage than the
 * clutter it costs. No smoothing or sensitivity parameter is needed at DFG
 * scale (tens of items, strictly decreasing tail), so this stays dependency-
 * free and exact.
 *
 * Reference numbers (trace_data.csv): activities knee = 8 of 16 (87.8%
 * coverage), edges knee = 9 of 24 (86.7%) — verified against the platform's
 * cached payload.
 */

export interface KneeResult {
  /** 1-based count of items at the knee (0 when the list is empty). */
  count: number;
  /** Coverage (0..1) achieved by keeping `count` items. */
  coverage: number;
  /** Frequency of the knee item — usable as a "keep if freq >= X" cutoff.
   *  Infinity when the list is empty (nothing passes). */
  thresholdFrequency: number;
}

/** Never auto-hide below this many activities (clamped to the total). */
export const AUTO_ACTIVITY_FLOOR = 5;

/**
 * Knee of the cumulative coverage curve. `freqsSortedDesc` MUST already be
 * sorted descending; the function does not re-sort (callers sort anyway for
 * their top-N logic and re-sorting here would hide contract violations).
 */
export function cumulativeCoverageKnee(freqsSortedDesc: number[]): KneeResult {
  const n = freqsSortedDesc.length;
  if (n === 0) return { count: 0, coverage: 0, thresholdFrequency: Infinity };

  let total = 0;
  for (const f of freqsSortedDesc) total += f;
  // All-zero frequencies: no curve to bend — keep everything.
  if (total <= 0) return { count: n, coverage: 1, thresholdFrequency: 0 };

  let best = -Infinity;
  let bestIdx = 0; // 0-based index of the knee item
  let cum = 0;
  for (let i = 0; i < n; i++) {
    cum += freqsSortedDesc[i]!;
    // Vertical distance from the cumulative curve to the y = x chord.
    const d = cum / total - (i + 1) / n;
    // Strict '>' keeps the FIRST maximum: the smallest item count that
    // achieves the best coverage-per-item trade-off.
    if (d > best) {
      best = d;
      bestIdx = i;
    }
  }

  let coverage = 0;
  for (let i = 0; i <= bestIdx; i++) coverage += freqsSortedDesc[i]!;
  return {
    count: bestIdx + 1,
    coverage: coverage / total,
    thresholdFrequency: freqsSortedDesc[bestIdx]!,
  };
}
