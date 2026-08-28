/**
 * The Phase-1 dataset contract + generic-visualization registry types.
 *
 * A *module dataset* (manifest `datasets:`) is fetched from the module's own
 * route and normalized (client-side, `adapters.ts`) into a shape-tagged
 * `DatasetEnvelope`. A `VizSpec` declares which shape(s) a generic
 * visualization accepts and how its fields bind to the dataset's columns
 * (Power-BI-style). The envelope + adapter are provisional (promoted
 * server-side in Phase 2); the envelope/viz TYPES are permanent.
 */

import type { ComponentType } from "react";

import type { WidgetConfigSchema } from "@/lib/dashboard-queries";

/** The five data shapes a dataset can take. Process-mining data is not purely
 * tabular (a DFG is a graph, a process tree a hierarchy), so the contract is a
 * small tagged union rather than "everything is a table". */
export type DatasetShape = "table" | "graph" | "kpi" | "tree" | "blob";

/** Column value types - drives which viz fields a column may bind to. */
export type ColumnType =
  | "number"
  | "integer"
  | "string"
  | "boolean"
  | "datetime"
  | "duration"
  | "enum";

/** Best-effort semantic role, used to auto-pick sensible default field bindings
 * (e.g. a bar chart defaults x→first dimension, y→first measure). */
export type FieldRole = "dimension" | "measure" | "time" | "id";

export interface ColumnSpec {
  id: string;
  label: string;
  type: ColumnType;
  role?: FieldRole;
}

/** Rows are objects keyed by column id (recharts `dataKey` + table cells). */
export interface TableData {
  columns: ColumnSpec[];
  rows: Array<Record<string, unknown>>;
}

export interface GraphNode {
  id: string;
  label: string;
  /** Frequency/weight, used to size + heat the node. */
  value?: number;
  /** Producer hint ("place" | "transition" | "activity" ...). */
  kind?: string;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  value?: number;
  label?: string;
  performanceSeconds?: number;
}

export interface GraphData {
  directed: boolean;
  nodes: GraphNode[];
  edges: GraphEdge[];
  start?: string[];
  end?: string[];
}

export type KpiFormat = "number" | "integer" | "percent" | "duration";

export interface KpiMeasure {
  key: string;
  label: string;
  value: number | string | null;
  unit?: string;
  format?: KpiFormat;
}

export interface KpiData {
  items: KpiMeasure[];
}

export interface TreeNode {
  id: string;
  label: string;
  value?: number;
  children: TreeNode[];
}

export interface TreeData {
  root: TreeNode;
}

export interface BlobData {
  media: string;
  value: string;
}

export type DatasetPayload = TableData | GraphData | KpiData | TreeData | BlobData;

export interface DatasetEnvelope {
  shape: DatasetShape;
  /** Field-mapping reads `schema.columns`; meaningful for `table`, empty for
   * shapes that need no mapping (graph/kpi/tree/blob). */
  schema: { columns: ColumnSpec[] };
  data: DatasetPayload;
  meta?: {
    title?: string;
    sourceKind?: string;
    rowCount?: number;
    truncated?: boolean;
    /** A short note rendered when the dataset is empty/needs a prior step
     * (e.g. conformance with no run yet). */
    note?: string;
  };
}

// ── Visualization registry ─────────────────────────────────────────────────

/** One bindable field on a viz (e.g. x / y / series / value). `accepts` filters
 * which dataset columns the user may bind to it. */
export interface VizFieldDef {
  key: string;
  label: string;
  accepts: ColumnType[];
  /** Bind multiple columns (e.g. several measures on one chart). */
  multiple?: boolean;
  required?: boolean;
}

export interface VizComponentProps {
  dataset: DatasetEnvelope;
  mapping: FieldMapping;
  options: Record<string, unknown>;
}

export type VizComponent = ComponentType<VizComponentProps>;

export interface VizSpec {
  id: string;
  title: string;
  /** Lucide icon name. */
  icon: string;
  group: "chart" | "kpi" | "table" | "graph" | "tree";
  accepts: DatasetShape[];
  /** Empty ⇒ the viz needs no field-mapping (table, kpi-grid, process-map). */
  fields: VizFieldDef[];
  /** Non-field options, in the existing card `config_schema` dialect. */
  options?: WidgetConfigSchema;
  defaults: { w: number; h: number; minW: number; minH: number };
  Component: VizComponent;
}

/** A placed viz card's field bindings: viz field key → column id (or ids). */
export type FieldMapping = Record<string, string | string[] | undefined>;
