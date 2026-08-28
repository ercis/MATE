"use client";

import type { ReactNode } from "react";
import { Info } from "lucide-react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/cn";

/**
 * Plain-language ⓘ hover explanations for the five fidelity measures, mirroring
 * the Complexity module's `panel/info-hint.tsx` + `metric-info.tsx` (module
 * panels/widgets bundle separately, so no cross-module import). The global
 * TooltipProvider (apps/web/components/providers.tsx) supplies the Radix provider.
 *
 * Grounded in this module's paper — Kirchdorfer et al. (2024), "AgentSimulator:
 * An agent-based approach for data-driven business process simulation" (ICPM),
 * DOI 10.1109/ICPM63005.2024.10680660 (the manifest's `source[0]`) — which
 * evaluates simulated vs. real logs with exactly these five measures across
 * control-flow (NGD), time (AED/CED/RED) and congestion (CTD), all "lower is
 * better", attributing the distance formulations to Chapela-Campa et al. The
 * exact local computation (Wasserstein/EMD in hours; total-variation for NGD)
 * is documented in `metrics.py`. Every measure here has a paper-backed entry;
 * an unmapped key renders no hint (see `MetricInfoHint`).
 */

const PAPER = "AgentSimulator (Kirchdorfer et al., 2024)";
const DOI = "10.1109/ICPM63005.2024.10680660";

function InfoHint({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          aria-label={label}
          onClick={(e) => e.stopPropagation()}
          className={cn(
            "inline-flex shrink-0 cursor-help items-center justify-center rounded-full",
            "text-muted-foreground/70 transition-colors hover:text-foreground",
            "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
          )}
        >
          <Info className="h-3 w-3" aria-hidden="true" />
        </button>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs space-y-1 text-left leading-relaxed">
        {children}
      </TooltipContent>
    </Tooltip>
  );
}

interface MeasureInfo {
  title: string;
  /** Dimension the paper groups the measure under. */
  dimension: string;
  text: string;
}

export const METRIC_INFO: Record<string, MeasureInfo> = {
  NGD: {
    title: "N-Gram Distribution",
    dimension: "Control-flow",
    text:
      "Compares the frequencies of the activity n-grams (here every 3 consecutive activities) observed in the real vs. simulated logs — how faithfully the simulation reproduces the ordering of work. Reported as the total-variation distance between the two n-gram distributions, in [0, 1] (0 = identical control-flow).",
  },
  AEDD: {
    title: "Absolute Event Distribution",
    dimension: "Time",
    text:
      "Compares when events occur in absolute time: the Earth Mover's (Wasserstein) distance, in hours, between the real and simulated distributions of event timestamps. Captures whether the simulation spreads activity across the calendar like the real process.",
  },
  CEDD: {
    title: "Circadian Event Distribution",
    dimension: "Time",
    text:
      "Compares the time-of-day pattern of events: the Earth Mover's distance, in hours, between the real and simulated distributions of event hour-of-day. Checks whether the simulation respects daily rhythms and working hours.",
  },
  REDD: {
    title: "Relative Event Distribution",
    dimension: "Time",
    text:
      "Compares event timing measured from the start of each case: the Earth Mover's distance, in hours, between the real and simulated distributions of within-case event times. Isolates intra-case timing from where a case sits on the calendar.",
  },
  CTDD: {
    title: "Cycle Time Distribution",
    dimension: "Congestion",
    text:
      "Compares how long cases take end to end: the Earth Mover's distance, in hours, between the real and simulated case cycle-time distributions. Reflects processing times together with resource contention, so it gauges congestion.",
  },
};

/** ⓘ hint for a fidelity measure key; renders nothing when no entry is mapped. */
export function MetricInfoHint({ metricKey }: { metricKey: string }) {
  const info = METRIC_INFO[metricKey];
  if (!info) return null;
  return (
    <InfoHint label={`What does ${info.title} mean?`}>
      <p className="font-medium">
        {info.title} · {info.dimension}
      </p>
      <p>{info.text}</p>
      <p className="text-muted-foreground">Lower is better. Source: {PAPER}, DOI {DOI}.</p>
    </InfoHint>
  );
}
