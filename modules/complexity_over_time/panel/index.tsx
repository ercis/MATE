"use client";

import { useEffect, useMemo, useState } from "react";
import { Popover } from "radix-ui";
import { Check, ChevronsUpDown } from "lucide-react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceArea,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/cn";
import { formatDuration } from "@/lib/format";

import { MetricInfoHint } from "./metric-info";
import {
  useComplexityTimeseries,
  useDriftPeriods,
  type ComplexityMetrics,
  type DriftPeriod,
  type SliceMode,
  type SlicePoint,
  type TimeseriesParams,
} from "./queries";

// Friendly labels reused from the complexity panel's row labels.
const KPI_LABELS: Record<string, string> = {
  variant_entropy: "Variant entropy",
  normalized_variant_entropy: "Variant entropy (normalised)",
  sequence_entropy: "Sequence entropy",
  normalized_sequence_entropy: "Sequence entropy (normalised)",
  sequence_entropy_linear: "Sequence entropy (linear forgetting)",
  normalized_sequence_entropy_linear: "Sequence entropy (linear, normalised)",
  sequence_entropy_exponential: "Sequence entropy (exponential forgetting)",
  normalized_sequence_entropy_exponential:
    "Sequence entropy (exponential, normalised)",
  structure: "Structure",
  affinity: "Affinity",
  magnitude: "Magnitude (events)",
  support: "Support (cases)",
  variety: "Variety (distinct activities)",
  level_of_detail: "Level of detail",
  time_granularity_s: "Time granularity (s)",
  distinct_traces_pct: "Distinct traces %",
  deviation_from_random: "Deviation from random",
  lempel_ziv: "Lempel-Ziv complexity",
  pentland_task: "Pentland task complexity",
  pentland_process: "Pentland process complexity",
  trace_length_min: "Trace length (min)",
  trace_length_avg: "Trace length (avg)",
  trace_length_max: "Trace length (max)",
};

const GRANULARITIES: { value: string; label: string }[] = [
  { value: "auto", label: "Auto" },
  { value: "daily", label: "Daily" },
  { value: "weekly", label: "Weekly" },
  { value: "monthly", label: "Monthly" },
  { value: "quarterly", label: "Quarterly" },
  { value: "yearly", label: "Yearly" },
];

const DEFAULT_KPI = "variant_entropy";

// Per-line colours. The first five reuse the app's shadcn chart tokens so the
// palette matches the rest of the UI; the rest extend it for the rare case a
// user plots more than five metrics at once. We cycle if even that runs out.
const LINE_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
  "var(--primary)",
  "oklch(0.65 0.2 330)",
  "oklch(0.7 0.17 140)",
];

function lineColor(i: number): string {
  return LINE_COLORS[i % LINE_COLORS.length];
}

function kpiLabel(key: string): string {
  return KPI_LABELS[key] ?? key;
}

// Unit-bearing metric keys: `_s` are durations in seconds, `_pct` percentages;
// every other complexity metric is a dimensionless index.
function isDurationKey(key: string): boolean {
  return key.endsWith("_s");
}

function isPercentKey(key: string): boolean {
  return key.endsWith("_pct");
}

// Format one metric value for its key: durations human-readable, percentages
// with a `%`, everything else as a plain number (integers verbatim, else 3 dp).
function fmtMetric(key: string, v: number | null): string {
  if (v === null || !Number.isFinite(v)) return "–";
  if (isDurationKey(key)) return formatDuration(v);
  if (isPercentKey(key)) return `${v.toFixed(2)} %`;
  return Number.isInteger(v) ? String(v) : v.toFixed(3);
}

// ── Concept-drift overlay styling (colour by drift type) ──────────────────────
// Colours mirror cv4cdd's CATEGORY_INDEX in modules/cv4cdd/cv4cdd_core.py so the
// bands + legend match cv4cdd's own similarity-matrix overlay exactly.

const DRIFT_COLORS: Record<string, string> = {
  sudden: "rgb(255, 255, 255)", // white
  gradual: "rgb(30, 144, 255)", // dodgerblue
  incremental: "rgb(255, 0, 255)", // magenta
  recurring: "rgb(0, 255, 255)", // aqua
};
const DRIFT_FALLBACK_COLOR = "rgb(255, 255, 0)"; // yellow – cv4cdd's default

// Shared by the chart bands and the legend swatches so they always match.
const DRIFT_FILL_OPACITY = 0.18;
const DRIFT_STROKE_OPACITY = 0.6;

function driftColor(type: string): string {
  return DRIFT_COLORS[type.toLowerCase()] ?? DRIFT_FALLBACK_COLOR;
}

// `rgb(r, g, b)` → `rgba(r, g, b, alpha)`.
function withAlpha(rgb: string, alpha: number): string {
  const inner = rgb.slice(rgb.indexOf("(") + 1, rgb.lastIndexOf(")"));
  return `rgba(${inner}, ${alpha})`;
}

function driftLabel(type: string): string {
  return type ? type.charAt(0).toUpperCase() + type.slice(1) : "Drift";
}

interface DriftBand {
  key: string;
  x1: string;
  x2: string;
  type: string;
  color: string;
  confidence: number;
}

function toMs(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : t;
}

// Map each drift's [start, end] timestamp window onto the categorical x-axis by
// finding the slice bands it overlaps, then shade x1..x2 with the type colour.
// The chart's x-axis is the slice label, so drift bounds must snap to labels.
function buildDriftBands(slices: SlicePoint[], drifts: DriftPeriod[]): DriftBand[] {
  if (slices.length === 0 || drifts.length === 0) return [];
  const intervals = slices.map((s, i) => ({
    i,
    start: toMs(s.start),
    end: toMs(s.end),
  }));
  const n = slices.length;
  const bands: DriftBand[] = [];

  drifts.forEach((d, di) => {
    const a = toMs(d.start_timestamp);
    const b = toMs(d.end_timestamp);
    if (a === null || b === null) return;
    const lo = Math.min(a, b);
    const hi = Math.max(a, b);

    let first = -1;
    let last = -1;
    for (const iv of intervals) {
      if (iv.start === null || iv.end === null) continue;
      if (iv.start <= hi && iv.end >= lo) {
        if (first === -1) first = iv.i;
        last = iv.i;
      }
    }
    if (first === -1) return; // drift falls outside the charted time range

    // Extend the right edge by one band so a single-slice drift is still
    // visible and multi-slice drifts cover their last band fully.
    const x2Index = last + 1 < n ? last + 1 : last;
    bands.push({
      key: `drift-${di}`,
      x1: slices[first].label,
      x2: slices[x2Index].label,
      type: String(d.type),
      color: driftColor(String(d.type)),
      confidence: d.confidence,
    });
  });

  return bands;
}

export function ComplexityOverTimePanel({ logId }: { logId: string; moduleId: string }) {
  const [mode, setMode] = useState<SliceMode>("calendar");
  const [slices, setSlices] = useState(50);
  const [granularity, setGranularity] = useState("auto");
  const [windowDays, setWindowDays] = useState(30);
  const [stepDays, setStepDays] = useState(7);
  // Multiple KPIs may be plotted at once – one line each.
  const [selectedKpis, setSelectedKpis] = useState<string[]>([DEFAULT_KPI]);
  // Min-max each series to [0,1] so metrics on different scales (e.g. magnitude
  // vs. normalised entropy) stay comparable on one shared axis.
  const [normalize, setNormalize] = useState(true);

  const params = useMemo<TimeseriesParams>(() => {
    if (mode === "absolute") return { slices };
    if (mode === "sliding") return { window: windowDays, step: stepDays };
    return { granularity };
  }, [mode, slices, granularity, windowDays, stepDays]);

  const q = useComplexityTimeseries(logId, mode, params);
  const driftQuery = useDriftPeriods(logId);

  // Keep the KPI selection valid as the available metric keys change. Functional
  // update + identity short-circuit avoids a render loop on the fresh `[]` ref.
  const metricKeys = q.data?.metric_keys ?? [];
  useEffect(() => {
    if (metricKeys.length === 0) return;
    setSelectedKpis((prev) => {
      const valid = prev.filter((k) => metricKeys.includes(k));
      if (valid.length === prev.length) return prev;
      if (valid.length > 0) return valid;
      return [metricKeys.includes(DEFAULT_KPI) ? DEFAULT_KPI : metricKeys[0]];
    });
  }, [metricKeys]);

  // One row per slice carrying, per selected metric, both the raw value (for the
  // tooltip) and the display value (raw, or normalised when `normalize` is on).
  const chartData = useMemo<ChartDatum[]>(() => {
    // Only min-max normalise when *comparing* two or more metrics. A single
    // metric is always plotted raw so the Y-axis keeps its real scale + units –
    // normalising one series would silently collapse the axis to a unitless 0–1.
    const applyNorm = selectedKpis.length > 1 && normalize;
    const pts = q.data?.slices ?? [];
    const ranges: Record<string, { min: number; max: number }> = {};
    for (const key of selectedKpis) {
      let min = Infinity;
      let max = -Infinity;
      for (const s of pts) {
        const v = s.metrics?.[key as keyof ComplexityMetrics];
        if (typeof v === "number" && Number.isFinite(v)) {
          if (v < min) min = v;
          if (v > max) max = v;
        }
      }
      ranges[key] = { min, max };
    }
    return pts.map((s) => {
      const raw: Record<string, number | null> = {};
      const disp: Record<string, number | null> = {};
      for (const key of selectedKpis) {
        const v = s.metrics?.[key as keyof ComplexityMetrics];
        const num = typeof v === "number" && Number.isFinite(v) ? v : null;
        raw[key] = num;
        if (num === null) {
          disp[key] = null;
        } else if (applyNorm) {
          const { min, max } = ranges[key];
          disp[key] = max > min ? (num - min) / (max - min) : 0.5;
        } else {
          disp[key] = num;
        }
      }
      return { label: s.label, n_cases: s.n_cases, n_events: s.n_events, raw, disp };
    });
  }, [q.data, selectedKpis, normalize]);

  const hasPoints = useMemo(
    () => chartData.some((d) => selectedKpis.some((k) => d.raw[k] !== null)),
    [chartData, selectedKpis],
  );

  const driftBands = useMemo(
    () => buildDriftBands(q.data?.slices ?? [], driftQuery.data?.drifts ?? []),
    [q.data, driftQuery.data],
  );

  return (
    <div className="space-y-6">
      <ControlBar
        mode={mode}
        setMode={setMode}
        slices={slices}
        setSlices={setSlices}
        granularity={granularity}
        setGranularity={setGranularity}
        windowDays={windowDays}
        setWindowDays={setWindowDays}
        stepDays={stepDays}
        setStepDays={setStepDays}
        selectedKpis={selectedKpis}
        setSelectedKpis={setSelectedKpis}
        normalize={normalize}
        setNormalize={setNormalize}
        metricKeys={metricKeys}
      />

      <ChartBody
        isLoading={q.isLoading}
        isError={q.isError || (!q.isLoading && !q.data)}
        hasPoints={hasPoints}
        data={chartData}
        selectedKpis={selectedKpis}
        normalize={normalize}
        driftBands={driftBands}
        driftRan={driftQuery.data?.ran === true}
      />
    </div>
  );
}

// ── Control bar ───────────────────────────────────────────────────────────────

interface ControlBarProps {
  mode: SliceMode;
  setMode: (m: SliceMode) => void;
  slices: number;
  setSlices: (n: number) => void;
  granularity: string;
  setGranularity: (g: string) => void;
  windowDays: number;
  setWindowDays: (n: number) => void;
  stepDays: number;
  setStepDays: (n: number) => void;
  selectedKpis: string[];
  setSelectedKpis: (k: string[]) => void;
  normalize: boolean;
  setNormalize: (v: boolean) => void;
  metricKeys: string[];
}

function ControlBar(props: ControlBarProps) {
  const {
    mode,
    setMode,
    slices,
    setSlices,
    granularity,
    setGranularity,
    windowDays,
    setWindowDays,
    stepDays,
    setStepDays,
    selectedKpis,
    setSelectedKpis,
    normalize,
    setNormalize,
    metricKeys,
  } = props;

  return (
    <Card>
      <CardContent className="flex flex-wrap items-start gap-4">
        <Field label="Mode">
          <Select value={mode} onValueChange={(v) => setMode(v as SliceMode)}>
            <SelectTrigger className="w-44">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="absolute">Absolute (fixed count)</SelectItem>
              <SelectItem value="calendar">Calendar (relative)</SelectItem>
              <SelectItem value="sliding">Sliding window</SelectItem>
            </SelectContent>
          </Select>
        </Field>

        {mode === "absolute" && (
          <Field label="Number of slices">
            <NumberInput value={slices} min={2} max={2000} onCommit={setSlices} />
          </Field>
        )}

        {mode === "calendar" && (
          <Field label="Granularity">
            <Select value={granularity} onValueChange={setGranularity}>
              <SelectTrigger className="w-36">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {GRANULARITIES.map((g) => (
                  <SelectItem key={g.value} value={g.value}>
                    {g.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
        )}

        {mode === "sliding" && (
          <>
            <Field label="Window (days)">
              <NumberInput value={windowDays} min={1} onCommit={setWindowDays} />
            </Field>
            <Field
              label="Step (days)"
              hint="A step larger than the window leaves gaps in coverage."
            >
              <NumberInput value={stepDays} min={1} onCommit={setStepDays} />
            </Field>
          </>
        )}

        {selectedKpis.length > 1 && (
          <Field
            label="Normalise 0–1"
            hint="Min-max each metric so different scales line up. Tooltip keeps raw values."
          >
            <div className="flex h-9 items-center">
              <Switch checked={normalize} onCheckedChange={setNormalize} />
            </div>
          </Field>
        )}
        <Field label="Y-axis metrics">
          <KpiMultiSelect
            metricKeys={metricKeys}
            selected={selectedKpis}
            onChange={setSelectedKpis}
          />
        </Field>
      </CardContent>
    </Card>
  );
}

// Multi-select dropdown: a popover holding a scrollable, checkable metric list.
// Built on the raw radix `Popover` primitive (the shadcn wrapper isn't a bundler
// runtime-external), styled to match `components/ui/popover.tsx`.
function KpiMultiSelect({
  metricKeys,
  selected,
  onChange,
}: {
  metricKeys: string[];
  selected: string[];
  onChange: (k: string[]) => void;
}) {
  const toggle = (key: string) => {
    if (selected.includes(key)) {
      // Keep at least one metric selected so the chart never goes blank.
      if (selected.length === 1) return;
      onChange(selected.filter((k) => k !== key));
    } else {
      onChange([...selected, key]);
    }
  };

  const triggerLabel =
    selected.length === 0
      ? "Select metrics"
      : selected.length === 1
        ? kpiLabel(selected[0])
        : `${selected.length} metrics`;

  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <Button
          variant="outline"
          role="combobox"
          className="w-64 justify-between font-normal"
        >
          <span className="truncate">{triggerLabel}</span>
          <ChevronsUpDown className="ml-2 size-4 shrink-0 opacity-50" />
        </Button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align="end"
          sideOffset={4}
          className="z-50 w-64 origin-(--radix-popover-content-transform-origin) rounded-md border bg-popover p-1 text-popover-foreground shadow-md outline-hidden data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95"
        >
          <div className="max-h-72 overflow-y-auto">
            {metricKeys.map((key) => {
              const on = selected.includes(key);
              return (
                <button
                  key={key}
                  type="button"
                  onClick={() => toggle(key)}
                  className={cn(
                    "flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-sm outline-none hover:bg-accent hover:text-accent-foreground",
                    on && "font-medium",
                  )}
                >
                  <Check
                    className={cn("size-4 shrink-0", on ? "opacity-100" : "opacity-0")}
                  />
                  <span className="truncate">{kpiLabel(key)}</span>
                </button>
              );
            })}
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="min-w-[9rem] shrink-0 space-y-1.5">
      <Label className="text-xs text-muted-foreground">{label}</Label>
      {children}
      {hint && <p className="max-w-44 text-[11px] text-muted-foreground">{hint}</p>}
    </div>
  );
}

// Numeric input that only commits a valid number on blur / Enter, so the
// query refetches once the user is done typing rather than per keystroke.
function NumberInput({
  value,
  min,
  max,
  onCommit,
}: {
  value: number;
  min?: number;
  max?: number;
  onCommit: (n: number) => void;
}) {
  const [draft, setDraft] = useState(String(value));

  useEffect(() => {
    setDraft(String(value));
  }, [value]);

  const commit = () => {
    let n = Number(draft);
    if (!Number.isFinite(n)) {
      setDraft(String(value));
      return;
    }
    if (min != null) n = Math.max(min, n);
    if (max != null) n = Math.min(max, n);
    if (n !== value) onCommit(n);
    setDraft(String(n));
  };

  // `@/components/ui/input` isn't a bundler runtime-external, so we render a
  // native input with the host app's input classes (resolved at runtime).
  return (
    <input
      type="number"
      className="h-9 w-36 min-w-0 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 dark:bg-input/30"
      value={draft}
      min={min}
      max={max}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          (e.target as HTMLInputElement).blur();
        }
      }}
    />
  );
}

// ── Chart ─────────────────────────────────────────────────────────────────────

interface ChartDatum {
  label: string;
  n_cases: number;
  n_events: number;
  // Per selected metric: the real value (tooltip) and the plotted value (line).
  raw: Record<string, number | null>;
  disp: Record<string, number | null>;
}

function ChartBody({
  isLoading,
  isError,
  hasPoints,
  data,
  selectedKpis,
  normalize,
  driftBands,
  driftRan,
}: {
  isLoading: boolean;
  isError: boolean;
  hasPoints: boolean;
  data: ChartDatum[];
  selectedKpis: string[];
  normalize: boolean;
  driftBands: DriftBand[];
  driftRan: boolean;
}) {
  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-96 w-full" />
      </div>
    );
  }

  if (isError) {
    return (
      <Card>
        <CardContent className="text-sm text-muted-foreground">
          Could not load the complexity time series. Re-import the log or check
          the module&apos;s logs.
        </CardContent>
      </Card>
    );
  }

  if (!hasPoints) {
    return (
      <Card>
        <CardContent className="text-sm text-muted-foreground">
          No slice has enough cases to compute the selected{" "}
          {selectedKpis.length > 1 ? "metrics" : "metric"}. Try a coarser
          granularity, fewer slices, or a wider window.
        </CardContent>
      </Card>
    );
  }

  const multiScale = selectedKpis.length > 1 && normalize;
  // A raw single-metric axis carries that metric's own unit; only unit-format the
  // ticks when every plotted line shares it (mixed raw units → plain numbers).
  const durationAxis = !multiScale && selectedKpis.every(isDurationKey);
  const percentAxis = !multiScale && selectedKpis.every(isPercentKey);
  // Y-axis title: names the normalisation when lines are rescaled to 0–1, else
  // the shared unit (the tooltip always shows each metric's real value + unit).
  const yAxisLabel = multiScale
    ? "normalized 0–1"
    : durationAxis
      ? "duration"
      : percentAxis
        ? "%"
        : undefined;
  const heading =
    selectedKpis.length === 1
      ? `${kpiLabel(selectedKpis[0])} over time`
      : "Complexity metrics over time";

  return (
    <Card>
      <CardContent>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
          <h3 className="flex items-center gap-1.5 text-sm font-semibold">
            {heading}
            {selectedKpis.length === 1 && <MetricInfoHint metricKey={selectedKpis[0]} />}
          </h3>
          <DriftLegend driftBands={driftBands} driftRan={driftRan} />
        </div>
        <ResponsiveContainer width="100%" height={400}>
          <LineChart data={data} margin={{ top: 10, right: 16, left: 0, bottom: 16 }}>
            <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
            {/* Concept-drift periods (cv4cdd) shaded behind the line, by type. */}
            {driftBands.map((b) => (
              <ReferenceArea
                key={b.key}
                x1={b.x1}
                x2={b.x2}
                fill={b.color}
                fillOpacity={DRIFT_FILL_OPACITY}
                stroke={b.color}
                strokeOpacity={DRIFT_STROKE_OPACITY}
                ifOverflow="hidden"
              />
            ))}
            <XAxis
              dataKey="label"
              tick={{ fill: "var(--muted-foreground)", fontSize: 10 }}
              interval="preserveStartEnd"
              minTickGap={24}
              stroke="var(--border)"
            />
            <YAxis
              tick={{ fill: "var(--muted-foreground)", fontSize: 10 }}
              stroke="var(--border)"
              width={yAxisLabel ? (durationAxis ? 92 : 72) : durationAxis ? 76 : 56}
              domain={multiScale ? [0, 1] : undefined}
              tickFormatter={
                durationAxis
                  ? (v: number) => formatDuration(v)
                  : percentAxis
                    ? (v: number) => `${v}%`
                    : undefined
              }
              label={
                yAxisLabel
                  ? {
                      value: yAxisLabel,
                      angle: -90,
                      position: "insideLeft",
                      style: {
                        fill: "var(--muted-foreground)",
                        fontSize: 11,
                        textAnchor: "middle",
                      },
                    }
                  : undefined
              }
              allowDecimals
            />
            <Tooltip
              content={<KpiTooltip selectedKpis={selectedKpis} />}
            />
            <Legend
              wrapperStyle={{ fontSize: 11 }}
              iconType="plainline"
            />
            {selectedKpis.map((key, i) => (
              <Line
                key={key}
                type="monotone"
                dataKey={(d: ChartDatum) => d.disp[key] ?? null}
                name={kpiLabel(key)}
                stroke={lineColor(i)}
                strokeWidth={2}
                dot={false}
                connectNulls
                isAnimationActive={false}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
        <div className="mt-2 space-y-1">
          {multiScale && (
            <p className="text-[11px] text-muted-foreground">
              Y-axis normalised 0–1 per metric so different scales are
              comparable; the tooltip shows each metric&apos;s raw value.
            </p>
          )}
          {driftBands.length > 0 && (
            <p className="text-[11px] text-muted-foreground">
              Shaded bands mark concept-drift periods detected by CV4CDD,
              coloured by drift type.
            </p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

// ── Drift legend ──────────────────────────────────────────────────────────────

function DriftLegend({
  driftBands,
  driftRan,
}: {
  driftBands: DriftBand[];
  driftRan: boolean;
}) {
  if (driftBands.length === 0) {
    if (driftRan) {
      return (
        <span className="text-[11px] text-muted-foreground">
          No concept drift detected
        </span>
      );
    }
    return null;
  }

  // One swatch per distinct drift type present, with its occurrence count.
  const counts = new Map<string, number>();
  for (const b of driftBands) {
    counts.set(b.type, (counts.get(b.type) ?? 0) + 1);
  }

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 px-1 py-0.5">
      {[...counts.entries()].map(([type, count]) => (
        <span key={type} className="flex items-center gap-2 text-[11px]">
          {/* Swatch previews the on-chart band: same fill + stroke opacity. */}
          <span
            className="inline-block h-3 w-3 rounded-[3px]"
            style={{
              backgroundColor: withAlpha(driftColor(type), DRIFT_FILL_OPACITY),
              border: `1px solid ${withAlpha(driftColor(type), DRIFT_STROKE_OPACITY)}`,
            }}
          />
          <span className="text-muted-foreground">
            {driftLabel(type)}
            {count > 1 ? ` ×${count}` : ""}
          </span>
        </span>
      ))}
    </div>
  );
}

interface TooltipPayloadItem {
  payload: ChartDatum;
}

function KpiTooltip({
  active,
  payload,
  label,
  selectedKpis,
}: {
  active?: boolean;
  payload?: TooltipPayloadItem[];
  label?: string;
  selectedKpis: string[];
}) {
  if (!active || !payload || payload.length === 0) return null;
  const datum = payload[0].payload;
  return (
    <div className="rounded-md border border-border bg-card px-3 py-2 text-xs shadow-sm">
      <div className="mb-1 font-medium text-card-foreground">{label}</div>
      <div className="space-y-0.5">
        {selectedKpis.map((key, i) => (
          <div key={key} className="flex items-center gap-2 tabular-nums">
            <span
              className="inline-block h-2 w-2 shrink-0 rounded-full"
              style={{ backgroundColor: lineColor(i) }}
            />
            <span className="text-muted-foreground">{kpiLabel(key)}:</span>
            <span className="ml-auto pl-3">{fmtMetric(key, datum.raw[key] ?? null)}</span>
          </div>
        ))}
      </div>
      <div className="mt-1 text-muted-foreground tabular-nums">
        {datum.n_cases} cases · {datum.n_events} events
      </div>
    </div>
  );
}
