"use client";

import { useEffect, useMemo, useState } from "react";
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
import { AlertTriangle, GitCompareArrows, Layers } from "lucide-react";

import { cn } from "@/lib/cn";
import { formatDuration, formatNumber } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
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

import { DiffDfgCanvas, STATUS_COLOR } from "./DiffDfgCanvas";
import {
  useActivityDeltas,
  useComparisonLogs,
  useDfgOverlay,
  useSimilarity,
  useVariantDiff,
} from "./queries";
import type { LogSummary, MetricKey } from "./types";

// Stable per-log colour for charts / matrix headers (baseline is index 0).
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
  const logs = logsQuery.data ?? [];

  const labelFor = useMemo(() => {
    const map = new Map(logs.map((l) => [l.id, l.name]));
    return (id: string) => map.get(id) ?? id.slice(0, 8);
  }, [logs]);

  const others = logs.filter((l) => l.id !== logId);
  const [selected, setSelected] = useState<string[]>([]);
  const toggle = (id: string) =>
    setSelected((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]));

  const baselineName = labelFor(logId);
  const hasSelection = selected.length > 0;

  return (
    <div className="flex flex-col gap-4">
      <div className="rounded-lg border bg-muted/40 px-4 py-3">
        <div className="mb-2 flex items-center gap-2 text-sm">
          <GitCompareArrows className="h-4 w-4 text-muted-foreground" />
          <span className="text-muted-foreground">Baseline</span>
          <Badge variant="secondary" className="font-medium">
            {baselineName}
          </Badge>
          <span className="text-muted-foreground">compared against</span>
        </div>
        {logsQuery.isLoading ? (
          <Skeleton className="h-8 w-full" />
        ) : others.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            No other ready case-centric logs to compare against. Import a second log first.
          </p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {others.map((l) => {
              const on = selected.includes(l.id);
              return (
                <Button
                  key={l.id}
                  type="button"
                  variant={on ? "default" : "outline"}
                  size="sm"
                  className="cursor-pointer gap-1.5"
                  onClick={() => toggle(l.id)}
                >
                  <Layers className="h-3.5 w-3.5" />
                  {l.name}
                  {typeof l.cases_count === "number" && (
                    <span className="text-[10px] opacity-70 tabular-nums">
                      {formatNumber(l.cases_count)} cases
                    </span>
                  )}
                </Button>
              );
            })}
          </div>
        )}
      </div>

      {!hasSelection ? (
        <EmptyState
          icon={GitCompareArrows}
          title="Pick at least one log to compare"
          description="Select one or more logs above to see how their behaviour differs from the baseline."
        />
      ) : (
        <Tabs defaultValue="similarity" className="w-full">
          <TabsList>
            <TabsTrigger value="similarity">Similarity</TabsTrigger>
            <TabsTrigger value="map">Process map</TabsTrigger>
            <TabsTrigger value="variants">Variants</TabsTrigger>
            <TabsTrigger value="activities">Activity deltas</TabsTrigger>
          </TabsList>

          <TabsContent value="similarity" className="mt-3">
            <SimilarityTab logId={logId} others={selected} labelFor={labelFor} />
          </TabsContent>
          <TabsContent value="map" className="mt-3">
            <ProcessMapTab logId={logId} others={selected} labelFor={labelFor} />
          </TabsContent>
          <TabsContent value="variants" className="mt-3">
            <VariantsTab logId={logId} others={selected} labelFor={labelFor} />
          </TabsContent>
          <TabsContent value="activities" className="mt-3">
            <ActivityDeltasTab logId={logId} others={selected} labelFor={labelFor} />
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

type LabelFor = (id: string) => string;

function TabError({ message }: { message: string }) {
  return (
    <EmptyState icon={AlertTriangle} title="Could not compute comparison" description={message} />
  );
}

function pct(v: number): string {
  return `${(v * 100).toFixed(1)}%`;
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

function SimilarityTab({
  logId,
  others,
  labelFor,
}: {
  logId: string;
  others: string[];
  labelFor: LabelFor;
}) {
  const [metric, setMetric] = useState<MetricKey>("emd");
  const { data, isLoading, isError, error } = useSimilarity(logId, others);

  if (isLoading) return <Skeleton className="h-72 w-full" />;
  if (isError || !data) return <TabError message={(error as Error)?.message ?? "Unknown error"} />;

  const meta = METRICS.find((m) => m.key === metric)!;
  const matrix = data.metrics[metric];
  const ids = data.log_ids;

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
              {ids.map((id, i) => (
                <TableHead key={id} className="text-center text-xs" style={{ color: LOG_COLORS[i % LOG_COLORS.length] }}>
                  {labelFor(id)}
                  {i === 0 && <span className="ml-1 opacity-60">(baseline)</span>}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {ids.map((rowId, i) => (
              <TableRow key={rowId}>
                <TableHead className="whitespace-nowrap text-xs" style={{ color: LOG_COLORS[i % LOG_COLORS.length] }}>
                  {labelFor(rowId)}
                </TableHead>
                {ids.map((colId, j) => (
                  <TableCell
                    key={colId}
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
// Process map diff (baseline vs one selected comparison log)
// --------------------------------------------------------------------------

function ProcessMapTab({
  logId,
  others,
  labelFor,
}: {
  logId: string;
  others: string[];
  labelFor: LabelFor;
}) {
  const [overlay, setOverlay] = useState<string>(others[0] ?? "");
  useEffect(() => {
    if (!others.includes(overlay)) setOverlay(others[0] ?? "");
  }, [others, overlay]);

  const { data, isLoading, isError, error } = useDfgOverlay(logId, overlay || null);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-xs text-muted-foreground">Overlay baseline against</span>
        <Select value={overlay} onValueChange={setOverlay}>
          <SelectTrigger className="h-8 w-56 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {others.map((id) => (
              <SelectItem key={id} value={id}>
                {labelFor(id)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Legendish />
      </div>

      {isLoading ? (
        <Skeleton className="h-[640px] w-full rounded-xl" />
      ) : isError || !data ? (
        <TabError message={(error as Error)?.message ?? "Unknown error"} />
      ) : (
        <>
          <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
            <span>{data.counts.shared_edges} shared edges</span>
            <span style={{ color: STATUS_COLOR.only_a }}>{data.counts.only_a_edges} baseline-only</span>
            <span style={{ color: STATUS_COLOR.only_b }}>{data.counts.only_b_edges} comparison-only</span>
          </div>
          <DiffDfgCanvas data={data} />
        </>
      )}
    </div>
  );
}

function Legendish() {
  const items: { status: keyof typeof STATUS_COLOR; label: string }[] = [
    { status: "shared", label: "Shared" },
    { status: "only_a", label: "Baseline only" },
    { status: "only_b", label: "Comparison only" },
  ];
  return (
    <div className="ml-auto flex items-center gap-3 text-[11px] text-muted-foreground">
      {items.map((it) => (
        <span key={it.status} className="flex items-center gap-1.5">
          <span
            className="inline-block h-2.5 w-4 rounded-sm"
            style={{ background: STATUS_COLOR[it.status] }}
          />
          {it.label}
        </span>
      ))}
    </div>
  );
}

// --------------------------------------------------------------------------
// Variant diff matrix
// --------------------------------------------------------------------------

function VariantsTab({
  logId,
  others,
  labelFor,
}: {
  logId: string;
  others: string[];
  labelFor: LabelFor;
}) {
  const { data, isLoading, isError, error } = useVariantDiff(logId, others);

  if (isLoading) return <Skeleton className="h-96 w-full" />;
  if (isError || !data) return <TabError message={(error as Error)?.message ?? "Unknown error"} />;

  const ids = data.log_ids;
  return (
    <div className="space-y-2">
      <p className="text-xs text-muted-foreground">
        {formatNumber(data.total_variants)} distinct variants across all logs · showing the{" "}
        {data.variants.length} that diverge most from the baseline (share of cases).
      </p>
      <div className="overflow-x-auto rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="text-xs">Variant</TableHead>
              {ids.map((id, i) => (
                <TableHead key={id} className="text-right text-xs" style={{ color: LOG_COLORS[i % LOG_COLORS.length] }}>
                  {labelFor(id)}
                  {i === 0 && <span className="ml-1 opacity-60">(base)</span>}
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
                {ids.map((id, i) => {
                  const present = v.counts[i] > 0;
                  return (
                    <TableCell
                      key={id}
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

function ActivityDeltasTab({
  logId,
  others,
  labelFor,
}: {
  logId: string;
  others: string[];
  labelFor: LabelFor;
}) {
  const { data, isLoading, isError, error } = useActivityDeltas(logId, others);

  const chartData = useMemo(() => {
    if (!data) return [];
    return data.activities.slice(0, 15).map((row) => {
      const point: Record<string, number | string> = { activity: row.activity };
      data.log_ids.forEach((id, i) => {
        point[id] = Number((row.freq_shares[i] * 100).toFixed(2));
      });
      return point;
    });
  }, [data]);

  if (isLoading) return <Skeleton className="h-96 w-full" />;
  if (isError || !data) return <TabError message={(error as Error)?.message ?? "Unknown error"} />;

  const ids = data.log_ids;
  return (
    <div className="space-y-4">
      <div className="rounded-lg border bg-card p-4">
        <p className="mb-3 text-xs text-muted-foreground">
          Activity frequency share per log (top 15 by divergence from baseline).
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
            <Legend formatter={(id: string) => labelFor(id)} wrapperStyle={{ fontSize: 11 }} />
            {ids.map((id, i) => (
              <Bar key={id} dataKey={id} fill={LOG_COLORS[i % LOG_COLORS.length]} />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div className="overflow-x-auto rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="text-xs">Activity</TableHead>
              {ids.map((id, i) => (
                <TableHead key={id} className="text-right text-xs" style={{ color: LOG_COLORS[i % LOG_COLORS.length] }}>
                  {labelFor(id)}
                  {i === 0 && <span className="ml-1 opacity-60">(base)</span>}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.activities.slice(0, 40).map((row) => (
              <TableRow key={row.activity}>
                <TableCell className="text-xs">{row.activity}</TableCell>
                {ids.map((id, i) => (
                  <TableCell key={id} className="text-right text-xs tabular-nums">
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
