/**
 * Client-side `kind → DatasetEnvelope` normalizers (Phase 1).
 *
 * Existing module routes already return `kind`-tagged JSON and already honor
 * the ambient `X-FF-Event-Filter` header + result-cache variant, so a generic
 * viz card just fetches the route and normalizes the response here - no backend
 * change. The adapters are deliberately defensive (introspect arrays into
 * tables, numeric leaves into KPIs) so an unrecognized-but-well-shaped response
 * still renders. Promoted server-side in Phase 2.
 */

import type {
  ColumnSpec,
  ColumnType,
  DatasetEnvelope,
  DatasetShape,
  GraphData,
  GraphEdge,
  GraphNode,
  KpiData,
  KpiMeasure,
  TableData,
  TreeData,
  TreeNode,
} from "./types";

type Json = Record<string, unknown>;

function isObject(v: unknown): v is Json {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function prettify(key: string): string {
  return key
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .trim();
}

const DATE_RE = /^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2})?/;

function inferType(value: unknown): ColumnType {
  if (typeof value === "boolean") return "boolean";
  if (typeof value === "number") return Number.isInteger(value) ? "integer" : "number";
  if (typeof value === "string") {
    if (DATE_RE.test(value)) return "datetime";
    const n = Number(value);
    if (value.trim() !== "" && Number.isFinite(n)) return Number.isInteger(n) ? "integer" : "number";
    return "string";
  }
  return "string";
}

function roleFor(type: ColumnType): ColumnSpec["role"] {
  if (type === "number" || type === "integer" || type === "duration") return "measure";
  if (type === "datetime") return "time";
  return "dimension";
}

/** Build a typed table from an array of row objects, inferring columns from the
 * union of keys and the type from each column's first non-null value. */
export function tableFromRows(rows: Json[]): TableData {
  const keys: string[] = [];
  const seen = new Set<string>();
  for (const r of rows) {
    for (const k of Object.keys(r)) {
      if (!seen.has(k)) {
        seen.add(k);
        keys.push(k);
      }
    }
  }
  const columns: ColumnSpec[] = keys.map((k) => {
    const sample = rows.find((r) => r[k] != null)?.[k];
    const type = inferType(sample);
    return { id: k, label: prettify(k), type, role: roleFor(type) };
  });
  return { columns, rows };
}

/** Collect numeric leaves of an object (one level of nesting) into KPI measures. */
function measuresFrom(obj: Json, prefix = ""): KpiMeasure[] {
  const out: KpiMeasure[] = [];
  for (const [k, v] of Object.entries(obj)) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (typeof v === "number") {
      out.push({ key, label: prettify(k), value: v, format: kpiFormat(k, v) });
    } else if (isObject(v)) {
      out.push(...measuresFrom(v, key));
    }
  }
  return out;
}

function kpiFormat(key: string, value: number): KpiMeasure["format"] {
  const k = key.toLowerCase();
  if (/(fitness|precision|perc|percent|share|ratio|rate)/.test(k) && value >= 0 && value <= 1) {
    return "percent";
  }
  if (/(^n[_.]|count|total|num)/.test(k) && Number.isInteger(value)) return "integer";
  return Number.isInteger(value) ? "integer" : "number";
}

function envelope(
  shape: DatasetShape,
  data: DatasetEnvelope["data"],
  columns: ColumnSpec[] = [],
  meta?: DatasetEnvelope["meta"],
): DatasetEnvelope {
  return { shape, schema: { columns }, data, meta };
}

// ── graph ──────────────────────────────────────────────────────────────────

function graphFromDfg(json: Json): GraphData {
  const activities = Array.isArray(json.activities) ? (json.activities as Json[]) : [];
  const edges = Array.isArray(json.edges) ? (json.edges as Json[]) : [];
  const nodes: GraphNode[] = activities.map((a) => ({
    id: String(a.id),
    label: String(a.label ?? a.id),
    value: typeof a.frequency === "number" ? a.frequency : undefined,
    kind: "activity",
  }));
  const gEdges: GraphEdge[] = edges.map((e, i) => ({
    id: String(e.id ?? `e${i}`),
    source: String(e.source),
    target: String(e.target),
    value: typeof e.frequency === "number" ? e.frequency : undefined,
    performanceSeconds: typeof e.performance_seconds === "number" ? e.performance_seconds : undefined,
  }));
  return {
    directed: true,
    nodes,
    edges: gEdges,
    start: Object.keys((json.start_activities as Json) ?? {}),
    end: Object.keys((json.end_activities as Json) ?? {}),
  };
}

function graphFromPetri(json: Json): GraphData {
  const places = Array.isArray(json.places) ? (json.places as Json[]) : [];
  const transitions = Array.isArray(json.transitions) ? (json.transitions as Json[]) : [];
  const arcs = Array.isArray(json.arcs) ? (json.arcs as Json[]) : [];
  const nodes: GraphNode[] = [
    ...places.map((p) => ({ id: String(p.id), label: String(p.label ?? ""), kind: "place" })),
    ...transitions.map((t) => ({
      id: String(t.id),
      label: t.is_invisible ? "" : String(t.label ?? t.name ?? ""),
      kind: "transition",
    })),
  ];
  const edges: GraphEdge[] = arcs.map((a, i) => ({
    id: String(a.id ?? `a${i}`),
    source: String(a.source),
    target: String(a.target),
    value: typeof a.weight === "number" ? a.weight : undefined,
  }));
  return { directed: true, nodes, edges };
}

// ── tree ─────────────────────────────────────────────────────────────────

function treeFromProcessTree(json: Json): TreeData {
  let counter = 0;
  const walk = (node: Json): TreeNode => {
    const children = Array.isArray(node.children) ? (node.children as Json[]) : [];
    const label = (node.label as string) ?? (node.operator as string) ?? "·";
    return {
      id: String(node.id ?? `n${counter++}`),
      label,
      children: children.map(walk),
    };
  };
  const root = isObject(json.root) ? walk(json.root as Json) : { id: "root", label: "·", children: [] };
  return { root };
}

function treeFromPrefix(json: Json): TreeData {
  const flat = Array.isArray(json.nodes) ? (json.nodes as Json[]) : [];
  const byId = new Map<string, TreeNode>();
  for (const n of flat) {
    byId.set(String(n.id), {
      id: String(n.id),
      label: String(n.label ?? "start"),
      value: typeof n.frequency === "number" ? n.frequency : undefined,
      children: [],
    });
  }
  let root: TreeNode | null = null;
  for (const n of flat) {
    const node = byId.get(String(n.id))!;
    const parent = n.parent != null ? byId.get(String(n.parent)) : null;
    if (parent) parent.children.push(node);
    else root = root ?? node;
  }
  return { root: root ?? { id: "root", label: "start", children: [] } };
}

// ── public entry ───────────────────────────────────────────────────────────

/** Normalize a module route's response into the declared shape. `shape` comes
 * from the dataset catalog entry; `json` is the raw route response. */
export function normalize(shape: DatasetShape, json: unknown): DatasetEnvelope {
  const obj: Json = isObject(json) ? json : {};
  const kind = typeof obj.kind === "string" ? obj.kind : "";

  if (shape === "graph") {
    const g = kind === "petri_net" ? graphFromPetri(obj) : graphFromDfg(obj);
    return envelope("graph", g, [], { sourceKind: kind });
  }

  if (shape === "tree") {
    const t = kind === "prefix_tree" ? treeFromPrefix(obj) : treeFromProcessTree(obj);
    return envelope("tree", t, [], { sourceKind: kind });
  }

  if (shape === "kpi") {
    let items: KpiMeasure[] = [];
    let note: string | undefined;
    if (kind === "conformance") {
      if (obj.ran === false || !isObject(obj.kpis)) {
        note = "No conformance run yet - run conformance on this log first.";
      } else {
        items = measuresFrom(obj.kpis as Json);
      }
    } else if (isObject(obj.basic) || isObject(obj.enriched)) {
      // complexity_metrics: basic + enriched numeric leaves.
      items = [
        ...measuresFrom((obj.basic as Json) ?? {}),
        ...(isObject(obj.enriched) ? measuresFrom(obj.enriched as Json, "enriched") : []),
      ];
    } else {
      items = measuresFrom(obj);
    }
    return envelope("kpi", { items } satisfies KpiData, [], { sourceKind: kind, note });
  }

  if (shape === "blob") {
    const value = typeof obj.xml === "string" ? obj.xml : typeof obj.value === "string" ? obj.value : "";
    return envelope("blob", { media: kind === "bpmn" ? "bpmn-xml" : "text", value }, [], { sourceKind: kind });
  }

  // table (default): prefer the known slice, else the first array-of-objects.
  let rows: Json[] = [];
  let note: string | undefined;
  if (kind === "conformance") {
    rows = Array.isArray(obj.per_activity) ? (obj.per_activity as Json[]) : [];
    if (rows.length === 0 && obj.ran === false) note = "No conformance run yet.";
  } else if (Array.isArray(obj.drifts)) {
    rows = obj.drifts as Json[];
  } else {
    for (const v of Object.values(obj)) {
      if (Array.isArray(v) && v.length > 0 && isObject(v[0])) {
        rows = v as Json[];
        break;
      }
    }
  }
  const table = tableFromRows(rows);
  return envelope("table", table, table.columns, {
    sourceKind: kind,
    rowCount: rows.length,
    note: rows.length === 0 ? (note ?? "No rows.") : undefined,
  });
}
