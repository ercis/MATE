"use client";

import {
  CartesianGrid,
  Cell,
  Funnel,
  FunnelChart,
  LabelList,
  Legend,
  PolarAngleAxis,
  PolarGrid,
  Radar,
  RadarChart,
  RadialBar,
  RadialBarChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  Treemap,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";

import { formatNumber } from "@/lib/format";
import type { KpiData, KpiMeasure, VizComponentProps } from "@/lib/visualizations/types";
import { VizEmpty } from "@/components/visualizations/viz-shell";
import {
  asTable,
  categoryCols,
  CHART_COLORS,
  labelOf,
  numericCols,
  resolveValue,
} from "./chart-common";

const numFmt = (v: unknown) => (typeof v === "number" ? formatNumber(v) : String(v ?? ""));
const AXIS = { tick: { fontSize: 10 }, stroke: "currentColor", className: "text-muted-foreground" } as const;

function kpiItems(dataset: VizComponentProps["dataset"]): KpiMeasure[] {
  return dataset.shape === "kpi" ? (dataset.data as KpiData).items : [];
}

function fmtMeasure(m: KpiMeasure): string {
  if (m.value == null) return "—";
  if (typeof m.value === "string") return m.value;
  if (m.format === "percent") return `${(m.value * 100).toFixed(1)}%`;
  return formatNumber(m.value);
}

/** Single headline number (the first KPI measure). */
export function NumberTileViz({ dataset }: VizComponentProps) {
  const m = kpiItems(dataset)[0];
  if (!m) return <VizEmpty message={dataset.meta?.note} />;
  return (
    <div className="flex h-full flex-col items-center justify-center text-center">
      <p className="text-xs text-muted-foreground">{m.label}</p>
      <p className="mt-1 text-4xl font-bold tabular-nums">{fmtMeasure(m)}</p>
    </div>
  );
}

/** Radial gauge for the first KPI measure (best for percent measures). */
export function GaugeViz({ dataset }: VizComponentProps) {
  const items = kpiItems(dataset);
  const m = items[0];
  if (!m || typeof m.value !== "number") {
    return <VizEmpty message={dataset.meta?.note ?? "Needs a numeric KPI."} />;
  }
  const total = items.reduce((s, i) => (typeof i.value === "number" ? s + i.value : s), 0) || 1;
  const frac =
    m.format === "percent"
      ? Math.max(0, Math.min(1, m.value))
      : Math.max(0, Math.min(1, m.value / total));
  const data = [{ name: m.label, value: frac * 100, fill: CHART_COLORS[0] }];
  return (
    <ResponsiveContainer width="100%" height="100%">
      <RadialBarChart innerRadius="68%" outerRadius="100%" data={data} startAngle={90} endAngle={-270}>
        <PolarAngleAxis type="number" domain={[0, 100]} tick={false} />
        <RadialBar dataKey="value" background cornerRadius={8} />
        <text x="50%" y="48%" textAnchor="middle" dominantBaseline="middle" className="fill-foreground text-xl font-bold">
          {fmtMeasure(m)}
        </text>
        <text x="50%" y="62%" textAnchor="middle" className="fill-muted-foreground text-[10px]">
          {m.label}
        </text>
      </RadialBarChart>
    </ResponsiveContainer>
  );
}

/** All KPI measures as concentric radial bars. */
export function RadialBarViz({ dataset }: VizComponentProps) {
  const items = kpiItems(dataset)
    .filter((i) => typeof i.value === "number")
    .slice(0, 8);
  if (!items.length) return <VizEmpty message={dataset.meta?.note} />;
  const max = Math.max(...items.map((i) => i.value as number), 1);
  const data = items.map((i, idx) => ({
    name: i.label,
    value: i.value as number,
    fill: CHART_COLORS[idx % CHART_COLORS.length],
  }));
  return (
    <ResponsiveContainer width="100%" height="100%">
      <RadialBarChart innerRadius="20%" outerRadius="100%" data={data}>
        <PolarAngleAxis type="number" domain={[0, max]} tick={false} />
        <RadialBar dataKey="value" cornerRadius={4} />
        <Legend iconSize={8} wrapperStyle={{ fontSize: 10 }} />
        <Tooltip formatter={(v) => numFmt(v)} contentStyle={{ fontSize: 12 }} />
      </RadialBarChart>
    </ResponsiveContainer>
  );
}

/** Radar/spider: a category column on the angles, one measure as the radius. */
export function RadarViz({ dataset, mapping }: VizComponentProps) {
  const table = asTable(dataset);
  const cols = table.columns;
  const cat = (typeof mapping.category === "string" && mapping.category) || categoryCols(cols)[0]?.id;
  const val = resolveValue(cols, mapping);
  if (!cat || !val || !table.rows.length) {
    return <VizEmpty message={dataset.meta?.note ?? "Map a category and a value."} />;
  }
  const data = table.rows.slice(0, 12).map((r) => ({ name: String(r[cat] ?? ""), value: Number(r[val]) || 0 }));
  return (
    <ResponsiveContainer width="100%" height="100%">
      <RadarChart data={data} cx="50%" cy="50%" outerRadius="75%">
        <PolarGrid className="stroke-border/40" />
        <PolarAngleAxis dataKey="name" tick={{ fontSize: 10 }} className="text-muted-foreground" />
        <Radar dataKey="value" stroke={CHART_COLORS[0]} fill={CHART_COLORS[0]} fillOpacity={0.4} />
        <Tooltip formatter={(v) => numFmt(v)} contentStyle={{ fontSize: 12 }} />
      </RadarChart>
    </ResponsiveContainer>
  );
}

/** Funnel: a category + value, sorted descending. */
export function FunnelViz({ dataset, mapping }: VizComponentProps) {
  const table = asTable(dataset);
  const cols = table.columns;
  const cat = (typeof mapping.category === "string" && mapping.category) || categoryCols(cols)[0]?.id;
  const val = resolveValue(cols, mapping);
  if (!cat || !val || !table.rows.length) {
    return <VizEmpty message={dataset.meta?.note ?? "Map a category and a value."} />;
  }
  const data = [...table.rows]
    .map((r) => ({ name: String(r[cat] ?? ""), value: Number(r[val]) || 0 }))
    .sort((a, b) => b.value - a.value)
    .slice(0, 10)
    .map((d, i) => ({ ...d, fill: CHART_COLORS[i % CHART_COLORS.length] }));
  return (
    <ResponsiveContainer width="100%" height="100%">
      <FunnelChart>
        <Tooltip formatter={(v) => numFmt(v)} contentStyle={{ fontSize: 12 }} />
        <Funnel dataKey="value" data={data} isAnimationActive>
          <LabelList position="right" dataKey="name" className="fill-muted-foreground text-[10px]" stroke="none" />
        </Funnel>
      </FunnelChart>
    </ResponsiveContainer>
  );
}

/** Treemap: a category + value (area-proportional tiles). */
export function TreemapViz({ dataset, mapping }: VizComponentProps) {
  const table = asTable(dataset);
  const cols = table.columns;
  const cat = (typeof mapping.category === "string" && mapping.category) || categoryCols(cols)[0]?.id;
  const val = resolveValue(cols, mapping);
  if (!cat || !val || !table.rows.length) {
    return <VizEmpty message={dataset.meta?.note ?? "Map a category and a value."} />;
  }
  const data = table.rows
    .map((r, i) => ({ name: String(r[cat] ?? ""), size: Number(r[val]) || 0, fill: CHART_COLORS[i % CHART_COLORS.length] }))
    .filter((d) => d.size > 0)
    .slice(0, 30);
  if (!data.length) return <VizEmpty />;
  return (
    <ResponsiveContainer width="100%" height="100%">
      <Treemap data={data} dataKey="size" stroke="var(--background)" isAnimationActive={false}>
        <Tooltip formatter={(v) => numFmt(v)} contentStyle={{ fontSize: 12 }} />
      </Treemap>
    </ResponsiveContainer>
  );
}

/** Bubble: x/y numeric + a size measure. */
export function BubbleViz({ dataset, mapping }: VizComponentProps) {
  const table = asTable(dataset);
  const cols = table.columns;
  const nums = numericCols(cols);
  const x = (typeof mapping.x === "string" && mapping.x) || nums[0]?.id;
  const y = (typeof mapping.y === "string" && mapping.y) || nums[1]?.id || nums[0]?.id;
  const size = (typeof mapping.size === "string" && mapping.size) || nums[2]?.id || nums[0]?.id;
  if (!x || !y || !table.rows.length) {
    return <VizEmpty message={dataset.meta?.note ?? "Needs numeric columns."} />;
  }
  return (
    <ResponsiveContainer width="100%" height="100%">
      <ScatterChart margin={{ top: 8, right: 16, bottom: 4, left: 4 }}>
        <CartesianGrid strokeDasharray="3 3" className="stroke-border/40" />
        <XAxis type="number" dataKey={x} name={labelOf(cols, x)} tickFormatter={numFmt} width={48} {...AXIS} />
        <YAxis type="number" dataKey={y} name={labelOf(cols, y)} tickFormatter={numFmt} width={48} {...AXIS} />
        <ZAxis type="number" dataKey={size} range={[30, 400]} name={labelOf(cols, size)} />
        <Tooltip cursor={{ strokeDasharray: "3 3" }} formatter={(v) => numFmt(v)} contentStyle={{ fontSize: 12 }} />
        <Scatter data={table.rows} fill={CHART_COLORS[0]} fillOpacity={0.6}>
          {table.rows.map((_, i) => (
            <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
          ))}
        </Scatter>
      </ScatterChart>
    </ResponsiveContainer>
  );
}
