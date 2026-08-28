/**
 * Manually-mirrored API types – the canonical source is FastAPI's
 * `/openapi.json`. Run `pnpm codegen` against a running backend to refresh.
 *
 * This file holds the minimum the frontend needs at the moment so we don't
 * block on having the backend up during build.
 */

export type EventLogStatus = "importing" | "processing" | "ready" | "failed";

/** Case-centric (XES/CSV/XML) vs object-centric (OCEL). The two are fully
 * isolated – drives the detail-page tabs, header counts, and which endpoints /
 * modules apply. */
export type LogModel = "case_centric" | "object_centric";

export interface EventLogSummary {
  id: string;
  name: string;
  status: EventLogStatus | string;
  source_format: string | null;
  source_filename: string | null;
  log_model: LogModel;
  events_count: number | null;
  /** Case-centric counts – null for object-centric logs. */
  cases_count: number | null;
  variants_count: number | null;
  /** Object-centric counts – null for case-centric logs. */
  objects_count: number | null;
  object_types_count: number | null;
  relations_count: number | null;
  date_min: string | null;
  date_max: string | null;
  error: string | null;
  folder_id: string | null;
  position: number;
  created_at: string;
  imported_at: string | null;
  last_edited_at: string | null;
  /** The importer had to guess a mandatory column – prompts a settings review. */
  mapping_needs_review?: boolean;
}

// ── Import wizard: staged upload + column probe (POST /event-logs/stage) ─────

/** How a canonical role was matched to a source column – the wizard's
 * confidence chip. `user` = the user picked it, `exact` = header matched a
 * canonical name/alias outright, `fuzzy` = substring match, `fallback` = guessed
 * from the data itself. */
export type ColumnRoleQuality = "user" | "exact" | "fuzzy" | "fallback";

export interface ProbeColumn {
  name: string;
  /** Fraction of sampled rows carrying a non-empty value (0–1). */
  coverage: number;
  samples: string[];
}

/** Result of staging an upload: the bytes are on the server (referenced by
 * `staging_token`), described well enough to confirm the mapping before the
 * import job is queued. */
export interface LogProbeResponse {
  staging_token: string;
  /** The sniffed format – a `.json` that turned out to be OCEL reads "ocel". */
  source_format: string;
  log_model: LogModel;
  /** False for object-centric logs, whose schema the OCEL parser owns. */
  needs_mapping: boolean;
  columns: ProbeColumn[];
  /** role → source column, from the same resolver the import job runs. */
  roles: Record<string, string>;
  quality: Record<string, ColumnRoleQuality>;
  events_sampled: number;
  size_bytes: number;
  filename: string | null;
  delimiter: string | null;
  event_element: string | null;
  event_path: string | null;
}

// ── Object-centric (OCEL) data shapes (GET /event-logs/{id}/ocel/*) ──────────

export interface OcelObjectTypeEntry {
  type: string;
  count: number;
}

export interface OcelOverview {
  events_count: number;
  objects_count: number;
  object_types_count: number;
  relations_count: number;
  date_min: string | null;
  date_max: string | null;
  object_types: OcelObjectTypeEntry[];
  activities: string[];
}

export interface OcelPage {
  rows: Record<string, unknown>[];
  columns: string[];
  total: number;
  offset: number;
  limit: number;
}

export type OcelObjectsPage = OcelPage;
export type OcelEventsPage = OcelPage;
export type OcelRelationsPage = OcelPage;

export interface FolderSummary {
  id: string;
  name: string;
  parent_id: string | null;
  position: number;
  created_at: string;
}

export interface ReorderItem {
  kind: "folder" | "log";
  id: string;
  parent_id: string | null;
  position: number;
}

export interface EventLogDetail extends EventLogSummary {
  detected_schema: Record<string, unknown> | null;
  description: string | null;
  column_overrides: EventLogColumnOverrides | null;
  /** The committed Events-tab filter applied to every module's view of the
   * log. `null` means the full dataset. */
  active_filter: FilterEntry[] | null;
  /** Resolved role → source-column mapping (case_id / activity / timestamp / …). */
  column_roles: Record<string, string> | null;
}

/** Manual column-role mapping submitted from settings → re-imports the log. */
export interface RemapColumnRoles {
  case_id: string;
  activity: string;
  timestamp: string;
  end_timestamp?: string | null;
  resource?: string | null;
  cost?: string | null;
  role?: string | null;
  lifecycle?: string | null;
}

export interface EventLogColumnOverrides {
  labels?: Record<string, string>;
  order?: string[];
  hidden?: string[];
  /** Display-only renames for activity values, applied at render time. The
   * underlying parquet keeps the raw activity names so analytics modules
   * continue to operate on canonical values. Managed from the Activities tab.
   */
  activity_labels?: Record<string, string>;
}

export interface EventLogCreateResponse {
  log_id: string;
  job_id: string;
}

export type JobStatus =
  | "queued"
  | "running"
  | "paused"
  | "completed"
  | "failed"
  | "cancelled";

export interface JobDetail {
  id: string;
  type: string;
  title: string;
  subtitle: string | null;
  module_id: string | null;
  payload_json: Record<string, unknown>;
  status: JobStatus | string;
  progress_current: number;
  progress_total: number | null;
  stage: string | null;
  message: string | null;
  error: string | null;
  rate: number | null;
  eta_seconds: number | null;
  priority: number;
  parent_job_id: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

/** One cited work (manifest `source[]`). `fullCitation` carries the authors. */
export interface ManifestSource {
  title: string;
  fullCitation: string;
  url: string | null;
}

/** One linked artifact (manifest `artifacts[]`) — repo, dataset, demo, model. */
export interface ManifestArtifact {
  name: string;
  url: string;
}

export interface ModuleSummary {
  id: string;
  name: string;
  version: string;
  category:
    | "foundation"
    | "attribute"
    | "external_input"
    | "advanced"
    | "comparison"
    | "other"
    | string;
  description: string | null;
  /** Longer "with this module you can …" text for the About info box. */
  about: string | null;
  /** Cited works (max 20) — title + full citation string + optional DOI link.
   *  There are no author fields: `fullCitation` carries the author names. */
  source: ManifestSource[];
  /** Optional named links (max 20) — code repo, dataset, demo, released model. */
  artifacts: ManifestArtifact[];
  license: string | null;
  provides: string[];
  consumes: string[];
  has_frontend: boolean;
  /** Whether the module page renders the log-scoped filter bar above the panel
   *  (manifest `frontend.log_filter`); false for panel-less modules. */
  supports_log_filter: boolean;
  enabled: boolean;
  is_confidential_safe: boolean;
  availability: { status: "available" | "unavailable" | "degraded"; reasons: string[] } | null;
}

export interface BusEnvelope<T = Record<string, unknown>> {
  topic: string;
  payload: T;
  ts: number;
}

// ── Events / Variants / Data quality / Edits ────────────────────────────────

export type ColumnType = "string" | "number" | "datetime" | "duration" | "enum" | "boolean";

export type ColumnRole =
  | "case_id"
  | "activity"
  | "timestamp"
  | "end_timestamp"
  | "resource"
  | "cost"
  | "role"
  | "lifecycle"
  | "custom";

export interface ColumnSpec {
  name: string;
  label: string;
  role: ColumnRole;
  type: ColumnType;
  nullable: boolean;
  required: boolean;
  enum_values: string[] | null;
}

export interface EventsHeader {
  events_count: number;
  cases_count: number;
  variants_count: number;
  date_min: string | null;
  date_max: string | null;
}

export type EventRow = Record<string, unknown> & { _has_missing?: boolean };

export interface EventsPage {
  rows: EventRow[];
  total: number;
  offset: number;
  limit: number;
  columns: ColumnSpec[];
  header: EventsHeader;
}

export type FilterOp =
  | "contains"
  | "equals"
  | "gte"
  | "lte"
  | "is_null"
  | "is_not_null"
  | "in";

export interface FilterEntry {
  field: string;
  op: FilterOp;
  /** A string array when `op` is "in" (the multi-select checklist); a scalar
   * for the single-value operators; omitted for is_null / is_not_null. */
  value?: string | number | boolean | string[] | null;
}

export interface ColumnValueEntry {
  value: string;
  count: number;
}

export interface ColumnValuesPage {
  field: string;
  values: ColumnValueEntry[];
  total_distinct: number;
  truncated: boolean;
}

export interface ActiveFilterResult {
  active_filter: FilterEntry[];
  modules_retriggered: boolean;
}

export interface CellPatch {
  field: string;
  value: unknown;
}

export interface CellPatchResult {
  row: EventRow;
  row_index: number;
  new_row_index: number;
  header: EventsHeader;
}

export interface BulkFillBody {
  row_indices: number[];
  field: string;
  value: unknown;
}

export interface BulkFillResult {
  updated: number;
  header: EventsHeader;
}

export interface VariantRow {
  rank: number;
  variant_id: string;
  activities: string[];
  case_count: number;
  case_pct: number;
  avg_duration_seconds: number | null;
  median_duration_seconds: number | null;
  first_seen: string | null;
  last_seen: string | null;
}

export interface VariantsPage {
  rows: VariantRow[];
  total: number;
  offset: number;
  limit: number;
}

export interface AttributeBreakdownEntry {
  value: unknown;
  count: number;
}

export interface AttributeBreakdown {
  column: string;
  label: string;
  top: AttributeBreakdownEntry[];
}

export interface VariantDetail {
  rank: number;
  variant_id: string;
  activities: string[];
  case_count: number;
  case_pct: number;
  avg_duration_seconds: number | null;
  median_duration_seconds: number | null;
  p90_duration_seconds: number | null;
  first_seen: string | null;
  last_seen: string | null;
  duration_histogram: number[];
  duration_bin_edges_seconds: number[];
  attribute_breakdowns: AttributeBreakdown[];
}

export interface VariantCase {
  case_id: string;
  case_start: string | null;
  case_end: string | null;
  case_duration_seconds: number | null;
  event_count: number;
}

export interface VariantCasesPage {
  rows: VariantCase[];
  total: number;
  offset: number;
  limit: number;
}

export interface ColumnQuality {
  column: string;
  label: string;
  type: ColumnType;
  role: ColumnRole;
  null_count: number;
  null_pct: number;
  distinct_count: number;
}

export interface DataQuality {
  total_events: number;
  columns: ColumnQuality[];
}

export interface ActivityRow {
  activity: string;
  count: number;
}

export interface ActivitiesPage {
  rows: ActivityRow[];
  total: number;
}

export interface ActivityDetail {
  activity: string;
  event_count: number;
  event_pct: number;
  case_count: number;
  case_pct: number;
  avg_occurrences_per_case: number | null;
  max_occurrences_per_case: number | null;
  start_case_count: number;
  start_case_pct: number;
  end_case_count: number;
  end_case_pct: number;
  first_seen: string | null;
  last_seen: string | null;
  variant_count: number;
  top_variants: VariantRow[];
}

export interface ActivityCase extends VariantCase {
  occurrences: number;
}

export interface ActivityCasesPage {
  rows: ActivityCase[];
  total: number;
  offset: number;
  limit: number;
}

export interface EventEditEntry {
  id: number;
  log_id: string;
  row_index: number;
  field: string;
  old_value_json: unknown;
  new_value_json: unknown;
  edited_at: string;
}

export interface EventEditsPage {
  rows: EventEditEntry[];
  total: number;
  offset: number;
  limit: number;
}

export interface EventLogUpdatePayload {
  name?: string;
  description?: string | null;
  column_overrides?: EventLogColumnOverrides | null;
  folder_id?: string | null;
  position?: number;
}

// ── Watched folders ─────────────────────────────────────────────────────────

export type WatchMode = "manual" | "interval" | "continuous";
export type WatchStatus = "active" | "paused" | "error";

/** Optional per-format column mapping forced on every imported file. */
export interface WatchDefaultMapping {
  csv_mapping?: Record<string, unknown>;
  xml_mapping?: Record<string, unknown>;
  json_mapping?: Record<string, unknown>;
}

export interface WatchedFolderSummary {
  id: string;
  name: string;
  source_path: string;
  mode: WatchMode | string;
  interval_seconds: number | null;
  status: WatchStatus | string;
  dest_folder_id: string | null;
  last_scanned_at: string | null;
  last_error: string | null;
  created_at: string;
  imported_count: number;
  failed_count: number;
}

export interface WatchedFileSummary {
  source_name: string;
  status: string;
  size: number | null;
  log_id: string | null;
  error: string | null;
  imported_at: string;
}

export interface WatchedFolderDetail extends WatchedFolderSummary {
  default_mapping: WatchDefaultMapping | null;
  files: WatchedFileSummary[];
}

export interface WatchedFolderCreatePayload {
  name: string;
  source_path?: string | null;
  mode: WatchMode;
  interval_seconds?: number | null;
  default_mapping?: WatchDefaultMapping | null;
  create_dest_folder?: boolean;
  dest_folder_id?: string | null;
}

export interface WatchedFolderUpdatePayload {
  name?: string;
  mode?: WatchMode;
  interval_seconds?: number | null;
  status?: "active" | "paused";
  default_mapping?: WatchDefaultMapping | null;
}

export interface ScanResponse {
  found: number;
  imported: number;
  skipped: number;
  failed: number;
}

// ── Admin behaviour-event export (GET /admin/export/*) ───────────────────────

/** Backend ``EventSource`` literal on AnalyticsEvent rows. */
export type EventSource = "client" | "server";

/** XES trace (case) notion for the event-log export. */
export type ExportCaseNotion = "session" | "user";

/** Output format for the behaviour export. The ``ocel-*`` variants download
 * the object-centric UI log as OCEL 2.0 (GET /admin/export/events-ocel2). */
export type ExportFormat = "xes" | "ndjson" | "csv" | "ocel-json" | "ocel-sqlite" | "ocel-xml";

/** Optional filters shared by every /admin/export/* route. Empty/undefined
 * fields add no predicate (the export then spans everything). Mirrors the
 * backend ``_event_filters`` params. */
export interface ExportFilters {
  user_id?: string | null;
  source?: EventSource | null;
  event_type?: string | null;
  event_name?: string | null;
  path_prefix?: string | null;
  /** ISO-8601; half-open [start, end) window on occurred_at. */
  start?: string | null;
  end?: string | null;
  session_id?: string | null;
}

export interface ExportTypeCount {
  label: string;
  count: number;
}

/** GET /admin/export/preview – counts + span for the current filter set. */
export interface ExportPreview {
  matched_events: number;
  matched_sessions: number;
  distinct_users: number;
  date_min: string | null;
  date_max: string | null;
  event_types: ExportTypeCount[];
}

export interface ExportUserOption {
  id: string;
  email: string | null;
  preferred_username: string | null;
}

/** GET /admin/export/facets – dropdown options for the filter UI. */
export interface ExportFacets {
  users: ExportUserOption[];
  event_types: string[];
  event_names: ExportTypeCount[];
  paths: ExportTypeCount[];
}

// ── AI config (masked) – GET/PUT /ai/config ────────────────────────────────
// The GET response never carries an api_key (mirrors backend AiConfigOut). The
// key flows to the provider call server-side only; the form shows `*_key_set`
// and a "leave blank to keep" placeholder.

export type AiProviderId = "anthropic" | "openai" | "unigpt" | "custom";

export interface AiConfigOut {
  system_prompt: string;
  anthropic_base_url: string | null;
  openai_base_url: string | null;
  unigpt_base_url: string | null;
  custom_base_url: string | null;
  anthropic_key_set: boolean;
  openai_key_set: boolean;
  unigpt_key_set: boolean;
  custom_key_set: boolean;
  selected_provider: AiProviderId | null;
  selected_model: string | null;
  classifier_model: string | null;
  allow_process_data: boolean;
  /** When true, an admin has locked AI settings for all users (read-only). */
  controlled_by_admin: boolean;
}

// ── Admin control framework – /admin/controls ──────────────────────────────

export type ControlScope = "setting" | "card";
export type ControlMode = "user" | "admin";

/** GET /admin/controls/items – one controllable server setting, or one settings
 *  card of a module (config / ai / model, keyed "<module_id>:<card_id>"). */
export interface ControlItem {
  scope: ControlScope;
  key: string;
  label: string;
  description: string | null;
  control_mode: ControlMode | string;
  /** Whether the admin value has been set (never the secret itself). */
  admin_value_set: boolean;
  /** Echoed admin value for non-secret items (cards, analytics, concurrency). */
  admin_value: unknown | null;
  /** True when any secret (ai.config key) is stored in the admin value. */
  secret_set: boolean;
  /** Config card's JSON-schema so the editor can render inputs. */
  config_schema: Record<string, unknown> | null;
  // ── card scope only ──
  /** The module this card belongs to. */
  module_id?: string | null;
  /** Which card: "config" | "ai" | "model". */
  card_id?: string | null;
  /** The card's human title. */
  title?: string | null;
  /** model_store manifest (model card) so the editor knows accept/config_key. */
  model_store?: { title?: string; description?: string | null; accept?: string; config_key?: string } | null;
  /** ai_models manifest (ai card) so the editor renders the right selectors. */
  ai_models?: Record<string, unknown> | null;
}

export interface ControlItems {
  items: ControlItem[];
}

/** PUT /admin/controls/items/{scope}/{key}. */
export interface ControlUpdate {
  control_mode: ControlMode;
  admin_value?: unknown | null;
}

// ── Admin insights metric groups – /admin/insights/{users,storage,jobs,usage} ─

export interface InsightsDayCount {
  day: string;
  count: number;
}
export interface InsightsLabelCount {
  label: string;
  count: number;
}
export interface InsightsTopUser {
  user_id: string;
  email: string | null;
  username: string | null;
  count: number;
}

export interface LastSeenBucket {
  bucket: string;
  count: number;
}

/** GET /admin/insights/users */
export interface UsersInsights {
  days: number;
  user_count: number;
  active_users_in_range: number;
  onboarding_completed: number;
  onboarding_completion_pct: number;
  active_users_by_day: InsightsDayCount[];
  sessions_by_day: InsightsDayCount[];
  last_seen_buckets: LastSeenBucket[];
  top_users_by_events: InsightsTopUser[];
}

export interface StorageUserRow {
  user_id: string;
  email: string | null;
  username: string | null;
  log_count: number;
  events_total: number;
  disk_bytes: number | null;
}

export interface StorageLogRow {
  id: string;
  name: string;
  owner_id: string;
  events_count: number | null;
  cases_count: number | null;
}

/** GET /admin/insights/storage */
export interface StorageInsights {
  backend_mode: string;
  s3_used_bytes: number | null;
  s3_object_count: number | null;
  s3_quota_bytes: number | null;
  s3_error: string | null;
  total_logs: number;
  total_events: number;
  per_user: StorageUserRow[];
  largest_logs: StorageLogRow[];
  disk_included: boolean;
}

export interface JobsRuntimeStats {
  concurrency: number;
  live_workers: number;
  queue_depth: number;
  running: number;
  paused_users: number;
}

/** GET /admin/insights/jobs */
export interface JobsInsights {
  days: number;
  runtime: JobsRuntimeStats;
  by_status: InsightsLabelCount[];
  by_type: InsightsLabelCount[];
  failures_by_day: InsightsDayCount[];
  completions_by_day: InsightsDayCount[];
  avg_duration_by_day: InsightsDayCount[];
  avg_duration_seconds: number;
  slowest_seconds: number;
}

export interface ModuleUsageRow {
  module_id: string;
  installs: number;
  runs: number;
  avg_duration_seconds: number;
}

export interface AiUsage {
  chat_requests: number;
  guidance_requests: number;
  /** AI token counts / cost are not tracked anywhere – always false today. */
  tokens_tracked: boolean;
}

/** GET /admin/insights/usage */
export interface UsageInsights {
  days: number;
  installs_by_module: InsightsLabelCount[];
  modules: ModuleUsageRow[];
  most_used_module: string | null;
  ai: AiUsage;
}

// --- Admin → Modules (cross-user ownership dashboard + controls) -----------
// Mirrors apps/api/.../routes/admin_modules.py.

export interface AdminModuleOwner {
  user_id: string;
  email: string | null;
  username: string | null;
  /** "default" | "upload" | "admin" – best-effort provenance. */
  source: string | null;
  installed_at: string;
}

/** GET /admin/modules – one row per module known to the platform. */
export interface AdminModuleRow {
  id: string;
  name: string;
  version: string;
  category: string;
  has_frontend: boolean;
  /** Ships in the repo modules/ folder; always default, cannot be un-defaulted. */
  is_bundled: boolean;
  /** In the effective default set (bundled or admin-declared) – every user gets it. */
  is_default: boolean;
  default_locked: boolean;
  /** Withheld from new seeding: existing owners keep it, but users who don't have
   * it yet are no longer auto-seeded it. The only way to stop a bundled default
   * reaching future users. */
  withheld_from_new_users: boolean;
  owner_count: number;
  /** Earliest source="upload" owner – best-effort "who uploaded this". */
  uploaded_by: AdminModuleOwner | null;
  owners: AdminModuleOwner[];
}

// --- GET /system/resources (Admin → System, live CPU/RAM monitor) ----------

export interface PerCoreStat {
  index: number;
  current_pct: number;
  max_pct: number;
}

export interface CpuStat {
  current_pct: number;
  max_pct: number;
  cores_logical: number;
  cores_physical: number;
  per_core: PerCoreStat[];
}

export interface MemoryStat {
  used_bytes: number;
  total_bytes: number;
  max_used_bytes: number;
  current_pct: number;
}

export interface ResourceSample {
  ts: number;
  cpu_pct: number;
  mem_used_bytes: number;
}

export type ResourceBreakdownSource =
  | "module_subprocess"
  | "module_inproc"
  | "api_baseline"
  | "system"
  | "idle";

export interface ResourceBreakdownSlice {
  label: string;
  module_id: string | null;
  source: ResourceBreakdownSource;
  /** CPU breakdown: percent of the host (0-100). Memory breakdown: bytes. */
  value: number;
  estimated: boolean;
}

export interface RunningJobInfo {
  id: string;
  module_id: string | null;
  user_id: string;
  type: string;
  title: string;
}

/** GET /system/resources – live host CPU/RAM + per-source breakdown (admin only). */
export interface SystemResources {
  cpu: CpuStat;
  memory: MemoryStat;
  history: ResourceSample[];
  cpu_breakdown: ResourceBreakdownSlice[];
  memory_breakdown: ResourceBreakdownSlice[];
  running_jobs: RunningJobInfo[];
  sample_interval_seconds: number;
  history_window_seconds: number;
}

/** GET /api/v1/api-tokens item – a per-user personal access token (no secret). */
export interface ApiTokenInfo {
  id: string;
  name: string;
  token_prefix: string;
  scopes: string[];
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
  revoked: boolean;
}

/** POST /api/v1/api-tokens – adds the cleartext `token`, shown exactly once. */
export interface CreateTokenResponse extends ApiTokenInfo {
  token: string;
}

export interface McpScopeInfo {
  id: string;
  description: string;
}

export interface McpOAuthInfo {
  authorization_server: string;
  client_id: string | null;
  metadata_url: string;
}

/** GET /api/v1/api-tokens/mcp-info – MCP availability, endpoint, OAuth + scopes. */
export interface McpInfo {
  enabled: boolean;
  url: string;
  require_consent: boolean;
  consented: boolean;
  mint_allowed: boolean;
  /** Live write lock – write tools are refused (or not even registered when boot-forced). */
  read_only: boolean;
  /** Toolsets registered at boot (env MCP_TOOLSETS). */
  toolsets: string[];
  scopes_supported: McpScopeInfo[];
  oauth: McpOAuthInfo;
}

/** GET/PUT /api/v1/api-tokens/consent – per-user external-egress consent. */
export interface McpConsentState {
  required: boolean;
  consented: boolean;
}

/** GET/PUT /api/v1/system/mcp – admin live config. */
export interface McpAdminConfig {
  /** env MCP_ENABLED (mount happens at boot) */
  boot_enabled: boolean;
  /** live effective availability */
  enabled: boolean;
  mint_policy: string;
  /** env MCP_READ_ONLY (write tools not even registered) */
  boot_read_only: boolean;
  /** live effective write lock */
  read_only: boolean;
  /** registered at boot (env MCP_TOOLSETS) */
  toolsets: string[];
}

/** PUT /api/v1/system/mcp body – all fields optional, only the given ones change. */
export interface McpAdminUpdate {
  enabled?: boolean;
  mint_policy?: string;
  read_only?: boolean;
}

/** GET /api/v1/admin/api-tokens – org-wide token (admin). */
export interface AdminApiTokenInfo {
  id: string;
  user_id: string;
  user_email: string | null;
  name: string;
  token_prefix: string;
  created_at: string;
  last_used_at: string | null;
  revoked: boolean;
}
