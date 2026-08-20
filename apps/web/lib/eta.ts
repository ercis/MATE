/**
 * ETA computation per INSTRUCTIONS.md §7.9.4.
 *
 *     elapsed   = now - started_at
 *     rate      = ema(samples)            // events / second, α = 0.3
 *     remaining = (total - current) / rate
 *
 * The frontend prefers the backend's `eta_seconds` when present (a module can
 * override the heuristic with domain knowledge); the EMA is the fallback.
 */

const ALPHA = 0.3;
const MAX_SAMPLES = 20;

interface Sample {
  ts: number; // ms
  current: number;
}

export class EtaTracker {
  private samples: Sample[] = [];
  private rate: number | null = null;

  reset() {
    this.samples = [];
    this.rate = null;
  }

  observe(current: number): void {
    const now = Date.now();
    this.samples.push({ ts: now, current });
    if (this.samples.length > MAX_SAMPLES) this.samples.shift();
    if (this.samples.length < 2) return;
    const prev = this.samples[this.samples.length - 2];
    const last = this.samples[this.samples.length - 1];
    const dt = (last.ts - prev.ts) / 1000;
    if (dt <= 0) return;
    const inst = (last.current - prev.current) / dt;
    if (!Number.isFinite(inst) || inst < 0) return;
    this.rate = this.rate === null ? inst : ALPHA * inst + (1 - ALPHA) * this.rate;
  }

  estimateSeconds(total: number | null): number | null {
    if (total === null || total === undefined) return null;
    if (!this.rate || this.rate <= 0) return null;
    const last = this.samples[this.samples.length - 1];
    if (!last) return null;
    const remaining = total - last.current;
    if (remaining <= 0) return 0;
    return remaining / this.rate;
  }

  ratePerSecond(): number | null {
    return this.rate;
  }
}
