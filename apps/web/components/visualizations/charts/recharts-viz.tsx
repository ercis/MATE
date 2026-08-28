"use client";

import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { formatNumber } from "@/lib/format";
import type { VizComponentProps } from "@/lib/visualizations/types";
import { VizEmpty } from "@/components/visualizations/viz-shell";
import {
  asTable,
  buildChartData,
  categoryCols,
  CHART_COLORS,
  labelOf,
  numericCols,
  resolveSeries,
  resolveValue,
  resolveX,
  resolveYs,
} from "./chart-common";

const AXIS = {
  tick: { fontSize: 10 },
  stroke: "currentColor",
  className: "text-muted-foreground",
} as const;
const TOOLTIP = { contentStyle: { fontSize: 12 } } as const;
const MARGIN = { top: 8, right: 16, bottom: 4, left: 4 } as const;

const numFmt = (v: unknown) => (typeof v === "number" ? formatNumber(v) : String(v ?? ""));

/** Shared bar/line/area renderer: x category/time on the axis, one or more y
 * measures (or a pivoted `series`) as colored series. */
function Cartesian({
  dataset,
  mapping,
  options,
  kind,
}: VizComponentProps & { kind: "bar" | "line" | "area" }) {
  const table = asTable(dataset);
  const cols = table.columns;
  const x = resolveX(cols, mapping);
  const ys = resolveYs(cols, mapping);
  const series = resolveSeries(cols, mapping);

  if (!x || ys.length === 0 || table.rows.length === 0) {
    return <VizEmpty message={dataset.meta?.note ?? "Map an X and a measure to plot."} />;
  }

  const { data, keys } = buildChartData(table, x, ys, series);
  const stackId = options.stacked ? "stack" : undefined;
  const showLegend = options.legend !== false && keys.length > 1;
  const nameFor = (k: string) => (series ? k : labelOf(cols, k));

  const body = keys.map((k, i) => {
    const color = CHART_COLORS[i % CHART_COLORS.length];
    if (kind === "bar") {
      return <Bar key={k} dataKey={k} name={nameFor(k)} fill={color} stackId={stackId} radius={[2, 2, 0, 0]} />;
    }
    if (kind === "line") {
      return <Line key={k} dataKey={k} name={nameFor(k)} stroke={color} dot={false} strokeWidth={2} />;
    }
    return (
      <Area
        key={k}
        dataKey={k}
        name={nameFor(k)}
        stroke={color}
        fill={color}
        fillOpacity={0.2}
        stackId={stackId}
      />
    );
  });

  const common = (
    <>
      <CartesianGrid strokeDasharray="3 3" className="stroke-border/40" />
      <XAxis dataKey={x} {...AXIS} />
      <YAxis tickFormatter={numFmt} width={48} {...AXIS} />
      <Tooltip {...TOOLTIP} formatter={(v) => numFmt(v)} />
      {showLegend && <Legend wrapperStyle={{ fontSize: 11 }} />}
      {body}
    </>
  );

  return (
    <ResponsiveContainer width="100%" height="100%">
      {kind === "bar" ? (
        <BarChart data={data} margin={MARGIN}>{common}</BarChart>
      ) : kind === "line" ? (
        <LineChart data={data} margin={MARGIN}>{common}</LineChart>
      ) : (
        <AreaChart data={data} margin={MARGIN}>{common}</AreaChart>
      )}
    </ResponsiveContainer>
  );
}

export function BarViz(props: VizComponentProps) {
  return <Cartesian {...props} kind="bar" />;
}
export function LineViz(props: VizComponentProps) {
  return <Cartesian {...props} kind="line" />;
}
export function AreaViz(props: VizComponentProps) {
  return <Cartesian {...props} kind="area" />;
}

export function ScatterViz({ dataset, mapping }: VizComponentProps) {
  const table = asTable(dataset);
  const cols = table.columns;
  const nums = numericCols(cols);
  const x = (typeof mapping.x === "string" && mapping.x) || nums[0]?.id;
  const y = (typeof mapping.y === "string" && mapping.y) || nums[1]?.id || nums[0]?.id;
  if (!x || !y || table.rows.length === 0) {
    return <VizEmpty message={dataset.meta?.note ?? "Needs two numeric columns."} />;
  }
  return (
    <ResponsiveContainer width="100%" height="100%">
      <ScatterChart margin={MARGIN}>
        <CartesianGrid strokeDasharray="3 3" className="stroke-border/40" />
        <XAxis type="number" dataKey={x} name={labelOf(cols, x)} tickFormatter={numFmt} width={48} {...AXIS} />
        <YAxis type="number" dataKey={y} name={labelOf(cols, y)} tickFormatter={numFmt} width={48} {...AXIS} />
        <Tooltip cursor={{ strokeDasharray: "3 3" }} {...TOOLTIP} formatter={(v) => numFmt(v)} />
        <Scatter data={table.rows} fill={CHART_COLORS[0]} />
      </ScatterChart>
    </ResponsiveContainer>
  );
}

export function PieViz({ dataset, mapping }: VizComponentProps) {
  const table = asTable(dataset);
  const cols = table.columns;
  const cat = (typeof mapping.category === "string" && mapping.category) || categoryCols(cols)[0]?.id || cols[0]?.id;
  const val = resolveValue(cols, mapping);
  if (!cat || !val || table.rows.length === 0) {
    return <VizEmpty message={dataset.meta?.note ?? "Map a category and a value."} />;
  }
  const data = table.rows
    .map((r) => ({ name: String(r[cat] ?? ""), value: Number(r[val]) || 0 }))
    .filter((d) => d.value > 0)
    .slice(0, 12);
  if (data.length === 0) return <VizEmpty />;
  return (
    <ResponsiveContainer width="100%" height="100%">
      <PieChart>
        <Tooltip {...TOOLTIP} formatter={(v) => numFmt(v)} />
        <Pie data={data} dataKey="value" nameKey="name" innerRadius="45%" outerRadius="80%" paddingAngle={1}>
          {data.map((_, i) => (
            <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
          ))}
        </Pie>
        <Legend wrapperStyle={{ fontSize: 11 }} />
      </PieChart>
    </ResponsiveContainer>
  );
}

export function HistogramViz({ dataset, mapping, options }: VizComponentProps) {
  const table = asTable(dataset);
  const cols = table.columns;
  const val = resolveValue(cols, mapping);
  if (!val || table.rows.length === 0) {
    return <VizEmpty message={dataset.meta?.note ?? "Map a numeric column to bin."} />;
  }
  const values = table.rows.map((r) => Number(r[val])).filter((v) => Number.isFinite(v));
  if (values.length === 0) return <VizEmpty />;
  const bins = Math.max(3, Math.min(30, Number(options.bins) || 10));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const width = (max - min) / bins || 1;
  const buckets = Array.from({ length: bins }, (_, i) => ({
    label: formatNumber(min + i * width),
    count: 0,
  }));
  for (const v of values) {
    let idx = Math.floor((v - min) / width);
    if (idx >= bins) idx = bins - 1;
    if (idx < 0) idx = 0;
    buckets[idx].count += 1;
  }
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={buckets} margin={MARGIN}>
        <CartesianGrid strokeDasharray="3 3" className="stroke-border/40" />
        <XAxis dataKey="label" {...AXIS} />
        <YAxis allowDecimals={false} tickFormatter={numFmt} width={40} {...AXIS} />
        <Tooltip {...TOOLTIP} formatter={(v) => [numFmt(v), "Count"]} />
        <Bar dataKey="count" fill={CHART_COLORS[0]} radius={[2, 2, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}
