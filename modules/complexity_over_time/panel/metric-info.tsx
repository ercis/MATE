"use client";

import { InfoHint } from "./info-hint";

/**
 * Plain-language hover explanations for the complexity measures this module
 * plots over time. Mirrors `modules/complexity/panel/metric-info.tsx` (module
 * panels bundle separately, so no cross-module imports) but keyed to the
 * over-time metric keys carried in each slice.
 *
 * Each measure is credited to the author who introduced it (as compiled in
 * Langer's thesis, §2.4.3–2.4.5), grounded in the papers under
 * `modules/complexity/papers/`:
 * - Augusto et al. (2022) — the EPA entropy measures (§3.2).
 * - Günther (2009), van der Aalst (2016), Pentland (2003) and Hærem et al.
 *   (2015) — the size / variation / distance measures.
 *
 * Measures with no definition in either paper (the linear/exponential
 * forgetting entropies, Pentland's task complexity) intentionally have no
 * entry — no hint is rendered for them.
 */

const AUGUSTO = "Augusto et al., 2022";
const GUNTHER = "Günther, 2009";
const VAN_DER_AALST = "van der Aalst, 2016";
const PENTLAND = "Pentland, 2003";
const HAEREM = "Hærem et al., 2015";

export interface MetricInfo {
  title: string;
  text: string;
  /** Author who introduced the measure (paper is in `modules/complexity/papers/`). */
  source: string;
}

export const METRIC_INFO: Record<string, MetricInfo> = {
  // ── Entropy ────────────────────────────────────────────────────────────────
  variant_entropy: {
    title: "Variant Entropy",
    text:
      "Graph entropy of the log's Extended Prefix Automaton (EPA), where traces sharing a prefix share states: |S|·log|S| minus the sum of |partition|·log|partition| over the automaton's state partitions. It measures how much the recorded behavior branches, considering only the structure — not how often each path is taken.",
    source: AUGUSTO,
  },
  normalized_variant_entropy: {
    title: "Variant Entropy (normalised)",
    text:
      "Variant entropy divided by its theoretical maximum |S|·log|S|, scaling it to [0,1] so slices whose automata differ in size can be compared.",
    source: AUGUSTO,
  },
  sequence_entropy: {
    title: "Sequence Entropy",
    text:
      "The same EPA graph entropy as variant entropy, but each partition is weighted by the number of events it contains instead of the number of states — so it also reflects how frequently each prefix occurs, and grows monotonically as events are added.",
    source: AUGUSTO,
  },
  normalized_sequence_entropy: {
    title: "Sequence Entropy (normalised)",
    text:
      "Sequence entropy divided by its theoretical maximum |seq(S)|·log|seq(S)|, scaling it to [0,1] for comparison across slices of different sizes.",
    source: AUGUSTO,
  },
  // ── Size / structure ──────────────────────────────────────────────────────
  magnitude: {
    title: "Magnitude",
    text:
      "Total count of events across all traces — introduced by Günther (2009) as the magnitude of an event log. A pure size measure: more recorded events make a larger, potentially more complex log.",
    source: GUNTHER,
  },
  support: {
    title: "Support",
    text:
      "Total number of traces (cases) in the log — Günther's (2009) support. A basic size measure counting recorded process executions.",
    source: GUNTHER,
  },
  variety: {
    title: "Variety",
    text:
      "Number of unique event types (activities) across all events and traces — Günther's (2009) variety. More distinct activities mean more behavior the process can exhibit.",
    source: GUNTHER,
  },
  level_of_detail: {
    title: "Level of Detail",
    text:
      "Counts the distinct event types within each trace and averages that over all traces (Günther, 2009). High values mean individual cases touch many different activities.",
    source: GUNTHER,
  },
  time_granularity_s: {
    title: "Time Granularity",
    text:
      "Smallest time difference between two consecutive events in a trace, averaged over all traces (Günther, 2009). It gauges how fine-grained the log's timestamps are — context for timestamp-sensitive analyses.",
    source: GUNTHER,
  },
  distinct_traces_pct: {
    title: "Distinct Traces",
    text:
      "Unique traces (activity sequences not repeated by any other case) divided by the total number of traces (van der Aalst, 2016). 100% means every case followed its own distinct path.",
    source: VAN_DER_AALST,
  },
  trace_length_min: {
    title: "Trace Length (min)",
    text:
      "Number of events in the shortest trace of the slice (van der Aalst, 2016) — a basic size measure derived from the event-log structure.",
    source: VAN_DER_AALST,
  },
  trace_length_avg: {
    title: "Trace Length (avg)",
    text:
      "Mean number of events per trace across the slice (van der Aalst, 2016) — a basic size measure derived from the event-log structure.",
    source: VAN_DER_AALST,
  },
  trace_length_max: {
    title: "Trace Length (max)",
    text:
      "Number of events in the longest trace of the slice (van der Aalst, 2016) — a basic size measure derived from the event-log structure.",
    source: VAN_DER_AALST,
  },
  // ── Variation / distance ──────────────────────────────────────────────────
  structure: {
    title: "Structure",
    text:
      "Observed directly-follows transitions between event types divided by the maximum possible number, inverted (Günther, 2009). Near 1 the behavior is sparse and structured; near 0 almost any activity can follow any other.",
    source: GUNTHER,
  },
  affinity: {
    title: "Affinity",
    text:
      "Similarity of the directly-follows transitions between unique traces, computed for all pairs and normalized over the unique traces (Günther, 2009). Homogeneous logs — traces sharing the same orderings — score high, signaling lower complexity.",
    source: GUNTHER,
  },
  deviation_from_random: {
    title: "Deviation from Random",
    text:
      "How much the observed transition matrix differs from fully random behavior: squared deviations from the equally-distributed baseline, aggregated and normalized (Pentland, 2003).",
    source: PENTLAND,
  },
  lempel_ziv: {
    title: "Lempel-Ziv Complexity",
    text:
      "Number of distinct sub-sequences (LZ76 phrases) needed to describe the event stream without repetition (Pentland, 2003). Repetitive behavior compresses into few phrases; random-looking behavior needs many.",
    source: PENTLAND,
  },
  pentland_process: {
    title: "Pentland's Process Complexity",
    text:
      "The number of acyclic paths implied by the log's directly-follows transition matrix, approximated as 10^(0.08·(1+e−v)) with e = distinct directly-follows edges and v = distinct event types (Hærem et al., 2015). More possible routes through the process mean more variation.",
    source: HAEREM,
  },
};

/** ⓘ hint for a metric key; renders nothing when no explanation is mapped. */
export function MetricInfoHint({
  metricKey,
  className,
}: {
  metricKey: string;
  className?: string;
}) {
  const info = METRIC_INFO[metricKey];
  if (!info) return null;
  return (
    <InfoHint label={`What does ${info.title} mean?`} className={className}>
      <p>{info.text}</p>
      <p className="text-muted-foreground">Source: {info.source}</p>
    </InfoHint>
  );
}
