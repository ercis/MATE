/**
 * The platform-owned generic visualization registry.
 *
 * Each `VizSpec` declares which dataset shape(s) it accepts, its field-mapping
 * (Power-BI-style column binding), optional non-field options (card
 * `config_schema` dialect), default sizing, and the React component. Rendered
 * by dashboard generic-viz cards.
 */

import type { ColumnType, DatasetShape, VizSpec } from "./types";

import { HeatmapViz } from "@/components/visualizations/charts/heatmap-viz";
import {
  AreaViz,
  BarViz,
  HistogramViz,
  LineViz,
  PieViz,
  ScatterViz,
} from "@/components/visualizations/charts/recharts-viz";
import {
  BubbleViz,
  FunnelViz,
  GaugeViz,
  NumberTileViz,
  RadarViz,
  RadialBarViz,
  TreemapViz,
} from "@/components/visualizations/charts/recharts-extra";
import { ProcessMapViz } from "@/components/visualizations/graphs/process-map";
import { KpiGridViz } from "@/components/visualizations/kpi/kpi-grid";
import { TableViz } from "@/components/visualizations/tables/table-viz";
import { TreeViz } from "@/components/visualizations/tree/tree-viz";

const NUM: ColumnType[] = ["number", "integer", "duration"];
const CAT: ColumnType[] = ["string", "boolean", "enum", "datetime"];

export const vizRegistry: Record<string, VizSpec> = {
  table: {
    id: "table",
    title: "Table",
    icon: "Table",
    group: "table",
    accepts: ["table"],
    fields: [],
    defaults: { w: 4, h: 8, minW: 2, minH: 5 },
    Component: TableViz,
  },
  "kpi-grid": {
    id: "kpi-grid",
    title: "KPI grid",
    icon: "LayoutGrid",
    group: "kpi",
    accepts: ["kpi"],
    fields: [],
    defaults: { w: 4, h: 8, minW: 2, minH: 5 },
    Component: KpiGridViz,
  },
  bar: {
    id: "bar",
    title: "Bar chart",
    icon: "BarChart3",
    group: "chart",
    accepts: ["table"],
    fields: [
      { key: "x", label: "Category (X)", accepts: CAT, required: true },
      { key: "y", label: "Measure (Y)", accepts: NUM, multiple: true, required: true },
      { key: "series", label: "Series (color)", accepts: CAT },
    ],
    options: { properties: { stacked: { type: "boolean", title: "Stacked" } } },
    defaults: { w: 4, h: 8, minW: 2, minH: 5 },
    Component: BarViz,
  },
  line: {
    id: "line",
    title: "Line chart",
    icon: "LineChart",
    group: "chart",
    accepts: ["table"],
    fields: [
      { key: "x", label: "X axis", accepts: CAT, required: true },
      { key: "y", label: "Measure (Y)", accepts: NUM, multiple: true, required: true },
      { key: "series", label: "Series (color)", accepts: CAT },
    ],
    defaults: { w: 4, h: 8, minW: 2, minH: 5 },
    Component: LineViz,
  },
  area: {
    id: "area",
    title: "Area chart",
    icon: "AreaChart",
    group: "chart",
    accepts: ["table"],
    fields: [
      { key: "x", label: "X axis", accepts: CAT, required: true },
      { key: "y", label: "Measure (Y)", accepts: NUM, multiple: true, required: true },
      { key: "series", label: "Series (color)", accepts: CAT },
    ],
    options: { properties: { stacked: { type: "boolean", title: "Stacked" } } },
    defaults: { w: 4, h: 8, minW: 2, minH: 5 },
    Component: AreaViz,
  },
  scatter: {
    id: "scatter",
    title: "Scatter plot",
    icon: "ScatterChart",
    group: "chart",
    accepts: ["table"],
    fields: [
      { key: "x", label: "X (numeric)", accepts: NUM, required: true },
      { key: "y", label: "Y (numeric)", accepts: NUM, required: true },
    ],
    defaults: { w: 3, h: 8, minW: 2, minH: 5 },
    Component: ScatterViz,
  },
  pie: {
    id: "pie",
    title: "Pie / donut",
    icon: "PieChart",
    group: "chart",
    accepts: ["table"],
    fields: [
      { key: "category", label: "Category", accepts: CAT, required: true },
      { key: "value", label: "Value", accepts: NUM, required: true },
    ],
    defaults: { w: 3, h: 8, minW: 2, minH: 5 },
    Component: PieViz,
  },
  histogram: {
    id: "histogram",
    title: "Histogram",
    icon: "BarChartBig",
    group: "chart",
    accepts: ["table"],
    fields: [{ key: "value", label: "Value", accepts: NUM, required: true }],
    options: {
      properties: {
        bins: {
          type: "integer",
          title: "Bins",
          minimum: 3,
          maximum: 30,
          step: 1,
          default: 10,
          ui: { widget: "slider" },
        },
      },
    },
    defaults: { w: 3, h: 8, minW: 2, minH: 5 },
    Component: HistogramViz,
  },
  heatmap: {
    id: "heatmap",
    title: "Heatmap",
    icon: "Grid3x3",
    group: "chart",
    accepts: ["table"],
    fields: [
      { key: "x", label: "X (category)", accepts: CAT, required: true },
      { key: "y", label: "Y (category)", accepts: CAT, required: true },
      { key: "value", label: "Value", accepts: NUM, required: true },
    ],
    defaults: { w: 4, h: 8, minW: 2, minH: 5 },
    Component: HeatmapViz,
  },
  "number-tile": {
    id: "number-tile",
    title: "Number",
    icon: "Hash",
    group: "kpi",
    accepts: ["kpi"],
    fields: [],
    defaults: { w: 2, h: 4, minW: 2, minH: 3 },
    Component: NumberTileViz,
  },
  gauge: {
    id: "gauge",
    title: "Gauge",
    icon: "Gauge",
    group: "kpi",
    accepts: ["kpi"],
    fields: [],
    defaults: { w: 3, h: 6, minW: 2, minH: 5 },
    Component: GaugeViz,
  },
  "radial-bar": {
    id: "radial-bar",
    title: "Radial bars",
    icon: "CircleDashed",
    group: "kpi",
    accepts: ["kpi"],
    fields: [],
    defaults: { w: 3, h: 8, minW: 2, minH: 5 },
    Component: RadialBarViz,
  },
  radar: {
    id: "radar",
    title: "Radar",
    icon: "Radar",
    group: "chart",
    accepts: ["table"],
    fields: [
      { key: "category", label: "Category", accepts: CAT, required: true },
      { key: "value", label: "Value", accepts: NUM, required: true },
    ],
    defaults: { w: 3, h: 8, minW: 2, minH: 5 },
    Component: RadarViz,
  },
  funnel: {
    id: "funnel",
    title: "Funnel",
    icon: "Filter",
    group: "chart",
    accepts: ["table"],
    fields: [
      { key: "category", label: "Stage", accepts: CAT, required: true },
      { key: "value", label: "Value", accepts: NUM, required: true },
    ],
    defaults: { w: 3, h: 8, minW: 2, minH: 5 },
    Component: FunnelViz,
  },
  treemap: {
    id: "treemap",
    title: "Treemap",
    icon: "LayoutDashboard",
    group: "chart",
    accepts: ["table"],
    fields: [
      { key: "category", label: "Category", accepts: CAT, required: true },
      { key: "value", label: "Value", accepts: NUM, required: true },
    ],
    defaults: { w: 4, h: 8, minW: 2, minH: 5 },
    Component: TreemapViz,
  },
  bubble: {
    id: "bubble",
    title: "Bubble",
    icon: "Circle",
    group: "chart",
    accepts: ["table"],
    fields: [
      { key: "x", label: "X (numeric)", accepts: NUM, required: true },
      { key: "y", label: "Y (numeric)", accepts: NUM, required: true },
      { key: "size", label: "Size (numeric)", accepts: NUM },
    ],
    defaults: { w: 3, h: 8, minW: 2, minH: 5 },
    Component: BubbleViz,
  },
  "process-map": {
    id: "process-map",
    title: "Process map",
    icon: "Workflow",
    group: "graph",
    accepts: ["graph"],
    fields: [],
    defaults: { w: 4, h: 12, minW: 3, minH: 8 },
    Component: ProcessMapViz,
  },
  tree: {
    id: "tree",
    title: "Tree",
    icon: "ListTree",
    group: "tree",
    accepts: ["tree"],
    fields: [],
    defaults: { w: 3, h: 10, minW: 2, minH: 6 },
    Component: TreeViz,
  },
};

/** The viz auto-selected when a dataset of `shape` is first dropped (needs no
 * field-mapping, so it renders immediately). */
export function defaultVizForShape(shape: DatasetShape): string {
  switch (shape) {
    case "graph":
      return "process-map";
    case "tree":
      return "tree";
    case "kpi":
      return "kpi-grid";
    default:
      return "table";
  }
}

/** Every viz that can render a dataset of `shape` - powers the viz picker. */
export function vizzesForShape(shape: DatasetShape): VizSpec[] {
  return Object.values(vizRegistry).filter((v) => v.accepts.includes(shape));
}
