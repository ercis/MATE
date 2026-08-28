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

/**
 * Human-readable label for a raw backend `stage` key. The ingest pipeline emits
 * terse keys (`parsing`/`normalizing`/`writing`/`done`); module install and
 * migration jobs emit a few more. This maps the known ones to friendly phrases
 * (e.g. import reads as "Reading data → Processing → Saving") and title-cases
 * anything unknown so a new stage still renders sensibly instead of shouting the
 * raw key in uppercase.
 */
const STAGE_LABELS: Record<string, string> = {
  // Event-log import (apps/api/.../ingest/dispatch.py)
  parsing: "Reading data",
  normalizing: "Processing",
  writing: "Saving",
  done: "Finishing up",
  // Module install / model / migration jobs
  extracting: "Extracting",
  installing: "Installing",
  migrating: "Migrating",
  validating: "Validating",
  ready: "Ready",
};

export function stageLabel(stage: string | null | undefined): string {
  if (!stage) return "";
  const known = STAGE_LABELS[stage.toLowerCase()];
  if (known) return known;
  // Fallback: turn "some_stage" / "some-stage" into "Some stage".
  const words = stage.replace(/[_-]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}
