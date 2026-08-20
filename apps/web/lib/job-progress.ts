import { formatNumber } from "@/lib/format";
import type { LiveJob } from "@/lib/stores/jobs";

export interface JobProgress {
  /** 0–100 when determinate, else null (indeterminate / pulsing bar). */
  pct: number | null;
  /** Human label for the progress trailer. */
  label: string;
  rate: number | null;
  eta: number | null;
}

/**
 * Derive the progress bar + label for a job. Three modes:
 *  - count   (`total` known):       "12,340 / 50,000 (24%)"
 *  - fraction (`total === 100`):     "24%"  – modules reporting a 0–1 fraction
 *      are mapped to 0–100 by the backend; we drop the redundant "n / 100".
 *      (A genuine 100-count job collides here cosmetically only.)
 *  - running counter (no `total`):   "12,340 processed" / "Estimating…"
 */
export function jobProgress(job: LiveJob): JobProgress {
  const total = job.progress_total ?? null;
  const current = job.progress_current;
  const rate = job.rate ?? job.rate_local ?? null;
  const eta = job.eta_seconds ?? job.eta_local ?? null;

  if (total && total > 0) {
    const pct = Math.min(100, Math.max(0, Math.floor((current / total) * 100)));
    const label =
      total === 100
        ? `${pct}%`
        : `${formatNumber(current)} / ${formatNumber(total)} (${pct}%)`;
    return { pct, label, rate, eta };
  }

  return {
    pct: null,
    label: current ? `${formatNumber(current)} processed` : "Estimating…",
    rate,
    eta,
  };
}
