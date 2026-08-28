/** Shapes returned by the conformance backend (mirrors `serializers.py`). */

export interface ConformanceKpis {
  log_fitness: number;
  /** null when the log exceeded the precision budget (see `precision_skipped`). */
  precision: number | null;
  perc_fit_traces: number;
  perc_fit_traces_pm4py: number | null;
  total_deviations: number;
  n_cases: number;
  n_variants: number;
}

export interface PerActivityDeviation {
  activity: string;
  deviations: number;
  log_moves: number;
  model_moves: number;
  cases_affected: number;
  matched: boolean;
}

export interface PerCaseDetail {
  cost?: number;
  log_moves?: string[];
  model_moves?: string[];
  missing_tokens?: number;
  remaining_tokens?: number;
  produced_tokens?: number;
  consumed_tokens?: number;
}

export interface PerCase {
  case_id: string;
  fitness: number;
  is_fit: boolean;
  n_deviations: number;
  deviations: string[];
  detail: PerCaseDetail;
}

export interface PerVariant {
  variant_id: string;
  activities: string[];
  n_cases: number;
  avg_fitness: number;
  deviations: number;
  detail: PerCaseDetail;
}

export interface LabelReport {
  in_model_not_log: string[];
  in_log_not_model: string[];
  matched: string[];
  model_count: number;
  log_count: number;
}

export type Technique = "token_replay" | "alignments";

export interface ConformanceResults {
  kind: "conformance";
  ran: boolean;
  has_model?: boolean;
  model_name?: string;
  model_hash?: string;
  technique: Technique;
  version?: number;
  kpis?: ConformanceKpis;
  per_activity?: PerActivityDeviation[];
  per_variant?: PerVariant[];
  per_case?: PerCase[];
  per_case_truncated?: boolean;
  label_report?: LabelReport;
  bpmn_xml?: string;
  /** Set when precision was skipped (log too large); KPI tile shows it as a note. */
  precision_skipped?: string | null;
}

export interface ModelInfo {
  name: string;
  size_bytes: number;
  uploaded_at: string | null;
  active: boolean;
}

export interface ModelsResponse {
  models: ModelInfo[];
  active: string | null;
}

export interface UploadResponse {
  name: string;
  size_bytes: number;
  tasks: number;
}
