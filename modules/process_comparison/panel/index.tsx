"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip as RTooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  AlertTriangle,
  ArrowDown,
  ArrowLeftRight,
  ArrowUp,
  GitCompareArrows,
  Layers,
  Minus,
} from "lucide-react";

import { cn } from "@/lib/cn";
import { formatDuration, formatNumber } from "@/lib/format";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { EmptyState } from "@/components/empty-state";
import {
  CanvasSettings,
  CanvasSettingsSegmented,
} from "@/components/visualizations/canvases/shared/canvas-toolbar";

import { ComparisonBpmnCanvas } from "./ComparisonBpmnCanvas";
import { buildActivityMap } from "./comparison-decorate";
import { DELTA_COLOR, DiffDfgCanvas, STATUS_COLOR, type DfgColorMode } from "./DiffDfgCanvas";
import { SideBySideDfg } from "./SideBySideDfg";
import {
  SidePicker,
  emptySide,
  sideLabel,
  toSide,
  type SideState,
} from "./SidePicker";
import {
  sidesReady,
  useActivityDeltas,
  useBpmnDiff,
  useComparisonLogs,
  useDfgOverlay,
  useSimilarity,
  useSummaryDelta,
  useVariantDiff,
} from "./queries";
import type { MetricKey, Side, SummaryKpi } from "./types";

// Stable per-side colour for charts / matrix headers (A is index 0).
const LOG_COLORS = [
  "rgb(37, 99, 235)", // blue-600
  "rgb(217, 119, 6)", // amber-600
  "rgb(5, 150, 105)", // emerald-600
  "rgb(219, 39, 119)", // pink-600
  "rgb(124, 58, 237)", // violet-600
  "rgb(8, 145, 178)", // cyan-600
];

export function ProcessComparisonPanel({ logId }: { logId: string; moduleId: string }) {
  const logsQuery = useComparisonLogs();
  const logs = useMemo(() => logsQuery.data ?? [], [logsQuery.data]);

  // A defaults to the log the panel was opened on; B to the first other log,
  // falling back to the same log (a same-log A/B is legal - the two filters are
  // what make it a comparison).
  const [sideA, setSideA] = useState<SideState>(() => emptySide(logId));
  const [sideB, setSideB] = useState<SideState>(() => emptySide(""));

  useEffect(() => {
    if (sideB.log || logs.length === 0) return;
    setSideB(emptySide(logs.find((l) => l.id !== logId)?.id ?? logId));
  }, [logs, logId, sideB.log]);

  const sides: Side[] = useMemo(() => [toSide(sideA), toSide(sideB)], [sideA, sideB]);
  const labels = useMemo(
    () => [sideLabel("A", sideA, logs), sideLabel("B", sideB, logs)],
    [sideA, sideB, logs],
  );
  const ready = sidesReady(sides);
  const bothPicked = Boolean(sideA.log && sideB.log);

  const swap = () => {
    setSideA(sideB);
    setSideB(sideA);
  };

  return (
    <div className="flex flex-col gap-4">
      {logsQuery.isLoading ? (
        <Skeleton className="h-32 w-full rounded-xl" />
      ) : logs.length === 0 ? (
        <EmptyState
          icon={GitCompareArrows}
          title="No comparable logs"
          description="Import a ready case-centric event log to start comparing."
        />
      ) : (
        <div className="grid items-start gap-3 lg:grid-cols-[1fr_auto_1fr]">
          <SidePicker
            letter="A · baseline"
            swatch={STATUS_COLOR.only_a}
            logs={logs}
            value={sideA}
            onChange={setSideA}
          />
          <div className="flex justify-center lg:pt-8">
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="cursor-pointer gap-1.5"
              onClick={swap}
              title="Swap A and B"
            >
              <ArrowLeftRight className="h-3.5 w-3.5" />
              Swap
            </Button>
          </div>
          <SidePicker
            letter="B · comparison"
            swatch={STATUS_COLOR.only_b}
            logs={logs}
            value={sideB}
            onChange={setSideB}
          />
        </div>
      )}

      {!ready ? (
        <EmptyState
          icon={GitCompareArrows}
          title={bothPicked ? "Both sides are identical" : "Pick two sides to compare"}
          description={
            bothPicked
              ? "A and B are the same log with the same filter, so there is nothing to diff. Change one side's log, or give it its own filter to compare two cohorts of the same log."
              : "Each side is a log plus its own filter. Point them at two logs — or at the same log with two different filters."
          }
        />
      ) : (
        <Tabs defaultValue="summary" className="w-full">
          <TabsList>
            <TabsTrigger value="summary">Summary</TabsTrigger>
            <TabsTrigger value="similarity">Similarity</TabsTrigger>
            <TabsTrigger value="map">Process map</TabsTrigger>
            <TabsTrigger value="bpmn">BPMN</TabsTrigger>
            <TabsTrigger value="variants">Variants</TabsTrigger>
            <TabsTrigger value="activities">Activity deltas</TabsTrigger>
          </TabsList>

          <TabsContent value="summary" className="mt-3">
            <SummaryTab logId={logId} sides={sides} labels={labels} />
          </TabsContent>
          <TabsContent value="similarity" className="mt-3">
            <SimilarityTab logId={logId} sides={sides} labels={labels} />
          </TabsContent>
          <TabsContent value="map" className="mt-3">
            <ProcessMapTab logId={logId} sides={sides} labels={labels} />
          </TabsContent>
          <TabsContent value="bpmn" className="mt-3">
            <BpmnTab logId={logId} sides={sides} labels={labels} />
          </TabsContent>
          <TabsContent value="variants" className="mt-3">
            <VariantsTab logId={logId} sides={sides} labels={labels} />
          </TabsContent>
          <TabsContent value="activities" className="mt-3">
            <ActivityDeltasTab logId={logId} sides={sides} labels={labels} />
          </TabsContent>
        </Tabs>
      )}
    </div>
  );
}

export default ProcessComparisonPanel;

// --------------------------------------------------------------------------
// Shared helpers
// --------------------------------------------------------------------------

/** Every tab renders the same two things: the compared sides and their labels.
 *  Labels are indexed by side (not by log id) because both sides may be the
 *  same log under different filters. */
interface TabProps {
  /** The log the panel was opened on - the route scope that binds ctx.cache. */
  logId: string;
  sides: Side[];
  labels: string[];
}

function TabError({ message }: { message: string }) {
  return (
    <EmptyState icon={AlertTriangle} title="Could not compute comparison" description={message} />
  );
}

function pct(v: number): string {
  return `${(v * 100).toFixed(1)}%`;
}

function PaneHeader({ name, swatch }: { name: string; swatch: string }) {
  return (
    <div className="flex items-center gap-1.5 text-xs font-medium">
      <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: swatch }} />
      <Layers className="h-3.5 w-3.5 text-muted-foreground" />
      {name}
    </div>
  );
}

// --------------------------------------------------------------------------
// Summary – headline KPI deltas (A vs B)
// --------------------------------------------------------------------------

function SummaryTab({ logId, sides, labels }: TabProps) {
  const { data, isLoading, isError, error } = useSummaryDelta(logId, sides);

  return (
    <div className="space-y-4">
      {isLoading ? (
        <Skeleton className="h-28 w-full" />
      ) : isError || !data ? (
        <TabError message={(error as Error)?.message ?? "Unknown error"} />
      ) : (
        <>
          <p className="text-xs text-muted-foreground">
            {labels[0]} → {labels[1]}
          </p>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
            {data.kpis.map((k) => (
              <KpiCard key={k.key} kpi={k} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function KpiCard({ kpi }: { kpi: SummaryKpi }) {
  const fmt = (v: number) => (kpi.unit === "seconds" ? formatDuration(v) : formatNumber(v));
  const color =
    kpi.delta === 0 ? DELTA_COLOR.same : kpi.delta > 0 ? DELTA_COLOR.up : DELTA_COLOR.down;
  const Arrow = kpi.delta > 0 ? ArrowUp : kpi.delta < 0 ? ArrowDown : Minus;
  const sign = kpi.delta > 0 ? "+" : kpi.delta < 0 ? "-" : "";

  return (
    <div className="rounded-lg border bg-card p-3">
      <div className="truncate text-xs text-muted-foreground" title={kpi.label}>
        {kpi.label}
      </div>
      <div className="mt-1 flex items-baseline gap-1.5">
        <span className="text-xs tabular-nums text-muted-foreground">{fmt(kpi.value_a)}</span>
        <span className="text-muted-foreground">→</span>
        <span className="text-lg font-semibold tabular-nums">{fmt(kpi.value_b)}</span>
      </div>
      <div className="mt-1 flex items-center gap-1 text-xs tabular-nums" style={{ color }}>
        <Arrow className="h-3.5 w-3.5" />
        <span>
          {sign}
          {fmt(Math.abs(kpi.delta))}
        </span>
        {kpi.pct_delta !== null && <span className="opacity-70">({pct(kpi.pct_delta)})</span>}
      </div>
      {kpi.lower_is_better && (
        <div className="mt-0.5 text-[10px] text-muted-foreground">lower is better</div>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------
// Similarity matrix
// --------------------------------------------------------------------------

const METRICS: { key: MetricKey; label: string; kind: "distance" | "similarity" }[] = [
  { key: "emd", label: "Earth Mover's Distance", kind: "distance" },
  { key: "footprints_similarity", label: "Footprints similarity", kind: "similarity" },
  { key: "variant_overlap", label: "Variant overlap", kind: "similarity" },
  { key: "edge_overlap", label: "Edge overlap", kind: "similarity" },
  { key: "activity_overlap", label: "Activity overlap", kind: "similarity" },
];

function SimilarityTab({ logId, sides, labels }: TabProps) {
  const [metric, setMetric] = useState<MetricKey>("emd");
  const { data, isLoading, isError, error } = useSimilarity(logId, sides);

  if (isLoading) return <Skeleton className="h-72 w-full" />;
  if (isError || !data) return <TabError message={(error as Error)?.message ?? "Unknown error"} />;

  const meta = METRICS.find((m) => m.key === metric)!;
  const matrix = data.metrics[metric];

  // Colour scale: greener = more similar. For a distance metric we invert.
  const cellBg = (v: number): string => {
    const sim = meta.kind === "distance" ? 1 - Math.min(1, v) : v;
    const alpha = 0.08 + 0.32 * sim;
    return `rgba(5, 150, 105, ${alpha.toFixed(3)})`;
  };
  const fmt = (v: number): string => (meta.kind === "distance" ? v.toFixed(3) : pct(v));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-xs text-muted-foreground">Metric</span>
        <Select value={metric} onValueChange={(v) => setMetric(v as MetricKey)}>
          <SelectTrigger className="h-8 w-56 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {METRICS.map((m) => (
              <SelectItem key={m.key} value={m.key}>
                {m.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <span className="text-xs text-muted-foreground">
          {meta.kind === "distance"
            ? "0 = identical behaviour, higher = more different."
            : "100% = identical, lower = more different."}
        </span>
      </div>

      <div className="overflow-x-auto rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="bg-muted/40" />
              {labels.map((label, i) => (
                <TableHead
                  key={i}
                  className="text-center text-xs"
                  style={{ color: LOG_COLORS[i % LOG_COLORS.length] }}
                >
                  {label}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {labels.map((label, i) => (
              <TableRow key={i}>
                <TableHead
                  className="whitespace-nowrap text-xs"
                  style={{ color: LOG_COLORS[i % LOG_COLORS.length] }}
                >
                  {label}
                </TableHead>
                {labels.map((_, j) => (
                  <TableCell
                    key={j}
                    className="text-center text-xs tabular-nums"
                    style={i === j ? undefined : { background: cellBg(matrix[i][j]) }}
                  >
                    {i === j ? <span className="text-muted-foreground">–</span> : fmt(matrix[i][j])}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------
// Process map diff (A vs B)
// --------------------------------------------------------------------------

function ProcessMapTab({ logId, sides, labels }: TabProps) {
  const [colorMode, setColorMode] = useState<DfgColorMode>("delta");
  const [layout, setLayout] = useState<"overlay" | "side">("overlay");

  const { data, isLoading, isError, error } = useDfgOverlay(logId, sides);

  // One settings body, rendered in every pane's control cluster – the layout
  // switch has to be reachable from whichever canvas the user is looking at.
  const settings = (
    <CanvasSettings>
      <CanvasSettingsSegmented
        label="Layout"
        value={layout}
        onChange={setLayout}
        options={[
          { value: "overlay", label: "Overlay" },
          { value: "side", label: "Side by side" },
        ]}
      />
      {layout === "overlay" && (
        <CanvasSettingsSegmented
          label="Colour"
          value={colorMode}
          onChange={setColorMode}
          options={[
            { value: "delta", label: "Δ change" },
            { value: "presence", label: "Presence" },
          ]}
        />
      )}
    </CanvasSettings>
  );

  return (
    <div className="space-y-3">
      {/* Legend only – every control lives in the canvas settings popover. */}
      <div className="flex flex-wrap items-center gap-3">
        <MapLegend mode={colorMode} layout={layout} />
      </div>

      {isLoading ? (
        // Inline height: module-only arbitrary Tailwind values (`h-[640px]`)
        // aren't emitted by the web app's build, so the skeleton would collapse.
        <Skeleton style={{ height: 640 }} className="w-full rounded-xl" />
      ) : isError || !data ? (
        <TabError message={(error as Error)?.message ?? "Unknown error"} />
      ) : layout === "side" ? (
        <div className="grid gap-3 lg:grid-cols-2">
          <div className="space-y-1.5">
            <PaneHeader name={labels[0]} swatch={STATUS_COLOR.only_a} />
            <SideBySideDfg data={data} side="a" settings={settings} />
          </div>
          <div className="space-y-1.5">
            <PaneHeader name={labels[1]} swatch={STATUS_COLOR.only_b} />
            <SideBySideDfg data={data} side="b" settings={settings} />
          </div>
        </div>
      ) : (
        <>
          <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
            <span>{data.counts.shared_edges} shared edges</span>
            <span style={{ color: STATUS_COLOR.only_a }}>{data.counts.only_a_edges} A-only</span>
            <span style={{ color: STATUS_COLOR.only_b }}>{data.counts.only_b_edges} B-only</span>
          </div>
          <DiffDfgCanvas data={data} mode={colorMode} settings={settings} />
        </>
      )}
    </div>
  );
}

function MapLegend({ mode, layout }: { mode: DfgColorMode; layout: "overlay" | "side" }) {
  const items =
    layout === "side"
      ? [
          { color: STATUS_COLOR.only_a, label: "A" },
          { color: STATUS_COLOR.only_b, label: "B" },
        ]
      : mode === "presence"
        ? [
            { color: STATUS_COLOR.shared, label: "Shared" },
            { color: STATUS_COLOR.only_a, label: "A only" },
            { color: STATUS_COLOR.only_b, label: "B only" },
          ]
        : [
            { color: DELTA_COLOR.up, label: "More / added" },
            { color: DELTA_COLOR.down, label: "Less / removed" },
            { color: DELTA_COLOR.same, label: "Unchanged" },
          ];

  return (
    <div className="ml-auto flex items-center gap-3 text-[11px] text-muted-foreground">
      {items.map((it) => (
        <span key={it.label} className="flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-4 rounded-sm" style={{ background: it.color }} />
          {it.label}
        </span>
      ))}
    </div>
  );
}

// --------------------------------------------------------------------------
// BPMN diff (one inductive-miner BPMN per side, side by side, delta-highlighted)
// --------------------------------------------------------------------------

function BpmnTab({ logId, sides, labels }: TabProps) {
  // "Stacked" drops the 2-column grid so each BPMN spans the full width. A large
  // model is otherwise squeezed into half the panel; each pane also keeps its own
  // fullscreen button (canvas control cluster) for an even bigger, isolated view.
  const [layout, setLayout] = useState<"side" | "single">("side");
  const { data, isLoading, isError, error } = useBpmnDiff(logId, sides);
  const map = useMemo(() => (data ? buildActivityMap(data.activities) : undefined), [data]);
  const paneHeight = layout === "single" ? 640 : 560;
  // Re-mount key: the canvas ignores xml updates after mount, so it must change
  // whenever EITHER side's log or filter changes - not just the log.
  const sideKey = useMemo(() => sides.map((s) => JSON.stringify(s)).join("~"), [sides]);

  const settings = (
    <CanvasSettings>
      <CanvasSettingsSegmented
        label="Layout"
        value={layout}
        onChange={setLayout}
        options={[
          { value: "side", label: "Side by side" },
          { value: "single", label: "Stacked" },
        ]}
      />
    </CanvasSettings>
  );

  return (
    <div className="space-y-3">
      {/* Legend only – the layout switch lives in each canvas's settings popover. */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="ml-auto flex items-center gap-3 text-[11px] text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <span
              className="inline-block h-2.5 w-4 rounded-sm"
              style={{ background: DELTA_COLOR.up }}
            />
            Added / grew
          </span>
          <span className="flex items-center gap-1.5">
            <span
              className="inline-block h-2.5 w-4 rounded-sm"
              style={{ background: DELTA_COLOR.down }}
            />
            Removed / shrank
          </span>
        </div>
      </div>

      {isLoading ? (
        <div className="space-y-2">
          <Skeleton className="h-4 w-40" />
          <Skeleton style={{ height: 560 }} className="w-full rounded-xl" />
          <p className="text-xs text-muted-foreground">
            Mining a BPMN model for each side — this can take a moment on large logs.
          </p>
        </div>
      ) : isError || !data ? (
        <TabError message={(error as Error)?.message ?? "Unknown error"} />
      ) : (
        <div className={cn("grid gap-3", layout === "side" && "lg:grid-cols-2")}>
          <BpmnPane
            name={labels[0]}
            swatch={STATUS_COLOR.only_a}
            xml={data.xml_a}
            paneKey={`${sideKey}-a`}
            map={map}
            height={paneHeight}
            refitKey={layout}
            settings={settings}
          />
          <BpmnPane
            name={labels[1]}
            swatch={STATUS_COLOR.only_b}
            xml={data.xml_b}
            paneKey={`${sideKey}-b`}
            map={map}
            height={paneHeight}
            refitKey={layout}
            settings={settings}
          />
        </div>
      )}
    </div>
  );
}

function BpmnPane({
  name,
  swatch,
  xml,
  paneKey,
  map,
  height,
  refitKey,
  settings,
}: {
  name: string;
  swatch: string;
  xml: string;
  paneKey: string;
  map: ReturnType<typeof buildActivityMap> | undefined;
  height: number;
  /** Changes when the pane is resized by the layout toggle → canvas re-fits. */
  refitKey: string | number;
  /** Popover body of this pane's canvas control cluster. */
  settings?: ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <PaneHeader name={name} swatch={swatch} />
      {/* Inline height: `h-[560px]` is a module-only arbitrary Tailwind value and
          the web app's build doesn't emit those, so the class was dropped and the
          pane collapsed to 0 height (the BPMN "rendered too small"). */}
      <div style={{ height }} className="w-full overflow-hidden rounded-xl border bg-card">
        {/* Re-mount on side change: the canvas ignores xml updates after mount. */}
        <ComparisonBpmnCanvas
          key={paneKey}
          xml={xml}
          map={map}
          refitKey={refitKey}
          settings={settings}
        />
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------
// Variant diff matrix
// --------------------------------------------------------------------------

function VariantsTab({ logId, sides, labels }: TabProps) {
  const { data, isLoading, isError, error } = useVariantDiff(logId, sides);

  if (isLoading) return <Skeleton className="h-96 w-full" />;
  if (isError || !data) return <TabError message={(error as Error)?.message ?? "Unknown error"} />;

  return (
    <div className="space-y-2">
      <p className="text-xs text-muted-foreground">
        {formatNumber(data.total_variants)} distinct variants across both sides · showing the{" "}
        {data.variants.length} that diverge most from A (share of cases).
      </p>
      <div className="overflow-x-auto rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="text-xs">Variant</TableHead>
              {labels.map((label, i) => (
                <TableHead
                  key={i}
                  className="text-right text-xs"
                  style={{ color: LOG_COLORS[i % LOG_COLORS.length] }}
                >
                  {label}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.variants.map((v, idx) => (
              <TableRow key={idx}>
                <TableCell className="max-w-md">
                  <span className="block truncate text-xs" title={v.label}>
                    {v.label}
                  </span>
                </TableCell>
                {labels.map((_, i) => {
                  const present = v.counts[i] > 0;
                  return (
                    <TableCell
                      key={i}
                      className={cn(
                        "text-right text-xs tabular-nums",
                        !present && "text-muted-foreground/40",
                      )}
                    >
                      {present ? (
                        <span>
                          {pct(v.shares[i])}
                          <span className="ml-1 opacity-50">({formatNumber(v.counts[i])})</span>
                        </span>
                      ) : (
                        "–"
                      )}
                    </TableCell>
                  );
                })}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------
// Activity frequency / sojourn deltas
// --------------------------------------------------------------------------

function ActivityDeltasTab({ logId, sides, labels }: TabProps) {
  const { data, isLoading, isError, error } = useActivityDeltas(logId, sides);

  // Series are keyed by side INDEX, not log id: both sides may be the same log,
  // and a duplicated recharts dataKey would collapse the two bars into one.
  const chartData = useMemo(() => {
    if (!data) return [];
    return data.activities.slice(0, 15).map((row) => {
      const point: Record<string, number | string> = { activity: row.activity };
      row.freq_shares.forEach((share, i) => {
        point[`s${i}`] = Number((share * 100).toFixed(2));
      });
      return point;
    });
  }, [data]);

  if (isLoading) return <Skeleton className="h-96 w-full" />;
  if (isError || !data) return <TabError message={(error as Error)?.message ?? "Unknown error"} />;

  return (
    <div className="space-y-4">
      <div className="rounded-lg border bg-card p-4">
        <p className="mb-3 text-xs text-muted-foreground">
          Activity frequency share per side (top 15 by divergence from A).
        </p>
        <ResponsiveContainer width="100%" height={320}>
          <BarChart data={chartData} margin={{ top: 8, right: 8, bottom: 40, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
            <XAxis
              dataKey="activity"
              angle={-30}
              textAnchor="end"
              interval={0}
              height={60}
              tick={{ fontSize: 10, fill: "var(--muted-foreground)" }}
            />
            <YAxis
              tick={{ fontSize: 10, fill: "var(--muted-foreground)" }}
              tickFormatter={(v) => `${v}%`}
            />
            <RTooltip formatter={(v) => `${v}%`} />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            {labels.map((label, i) => (
              <Bar
                key={i}
                dataKey={`s${i}`}
                name={label}
                fill={LOG_COLORS[i % LOG_COLORS.length]}
              />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div className="overflow-x-auto rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="text-xs">Activity</TableHead>
              {labels.map((label, i) => (
                <TableHead
                  key={i}
                  className="text-right text-xs"
                  style={{ color: LOG_COLORS[i % LOG_COLORS.length] }}
                >
                  {label}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.activities.slice(0, 40).map((row) => (
              <TableRow key={row.activity}>
                <TableCell className="text-xs">{row.activity}</TableCell>
                {labels.map((_, i) => (
                  <TableCell key={i} className="text-right text-xs tabular-nums">
                    <span>{pct(row.freq_shares[i])}</span>
                    <span className="ml-1 text-muted-foreground">
                      {row.avg_sojourn_s[i] > 0 ? formatDuration(row.avg_sojourn_s[i]) : "–"}
                    </span>
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
