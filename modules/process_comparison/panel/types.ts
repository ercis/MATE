// Response shapes returned by the process_comparison backend routes, plus the
// trimmed event-log summary the panel needs for its log picker.

import type { FilterEntry } from "@/lib/api-types";

export type DiffStatus = "shared" | "only_a" | "only_b";

/** One thing being compared: a log plus the row filter applied to it.
 *
 * A side - not a log - is the unit of comparison, which is what lets A and B
 * point at the SAME log under two different filters (cohort vs cohort). The
 * pair travels to every route base64-encoded on the `sides` query param. */
export interface Side {
  log: string;
  /** Replaces that log's committed Events-tab filter for this side's read.
   *  Empty = the raw log. Column entries and time-window entries share the
   *  list, exactly like the dashboard's filter header. */
  filter: FilterEntry[];
}

export interface SimilarityData {
  kind: "similarity";
  log_ids: string[];
  metrics: {
    emd: number[][];
    footprints_similarity: number[][];
    activity_overlap: number[][];
    edge_overlap: number[][];
    variant_overlap: number[][];
  };
}

export type MetricKey = keyof SimilarityData["metrics"];

export interface DfgDiffNode {
  id: string;
  label: string;
  status: DiffStatus;
  freq_a: number;
  freq_b: number;
  is_start: boolean;
  is_end: boolean;
}

export interface DfgDiffEdge {
  id: string;
  source: string;
  target: string;
  status: DiffStatus;
  freq_a: number;
  freq_b: number;
}

export interface DfgDiffData {
  kind: "dfg_diff";
  version: number;
  activities: DfgDiffNode[];
  edges: DfgDiffEdge[];
  start_activities: string[];
  end_activities: string[];
  counts: { shared_edges: number; only_a_edges: number; only_b_edges: number };
  baseline_log_id: string;
  other_log_id: string;
}

export interface VariantRow {
  activities: string[];
  label: string;
  counts: number[];
  shares: number[];
  in_baseline: boolean;
  max_abs_share_delta: number;
}

export interface VariantDiffData {
  kind: "variant_diff";
  log_ids: string[];
  totals: number[];
  total_variants: number;
  variants: VariantRow[];
}

export interface ActivityDeltaRow {
  activity: string;
  frequencies: number[];
  freq_shares: number[];
  avg_sojourn_s: number[];
  freq_share_delta_vs_baseline: number[];
}

export interface ActivityDeltasData {
  kind: "activity_deltas";
  log_ids: string[];
  activities: ActivityDeltaRow[];
}

export interface LogSummary {
  id: string;
  name: string;
  status: string;
  log_model: string;
  cases_count: number | null;
  events_count: number | null;
}

/** Only the field of the log detail the panel needs: the committed Events-tab
 *  filter, which seeds a side when that log is first picked. */
export interface LogFilterDetail {
  id: string;
  active_filter: FilterEntry[] | null;
}

// -- /summary -------------------------------------------------------------

export interface SummaryKpi {
  key: string;
  label: string;
  unit: "count" | "seconds";
  value_a: number;
  value_b: number;
  delta: number;
  /** null when the baseline value is 0 (no meaningful percentage). */
  pct_delta: number | null;
  /** true for throughput: a negative delta is an improvement. */
  lower_is_better: boolean;
}

export interface SummaryDeltaData {
  kind: "summary_delta";
  kpis: SummaryKpi[];
  baseline_log_id: string;
  other_log_id: string;
}

// -- /bpmn ----------------------------------------------------------------

export interface BpmnDiffData {
  kind: "bpmn_diff";
  xml_a: string;
  xml_b: string;
  /** Per-activity diff (same shape as the DFG nodes) so the BPMN overlay can
   *  colour each task by status / delta. */
  activities: DfgDiffNode[];
  baseline_log_id: string;
  other_log_id: string;
}
