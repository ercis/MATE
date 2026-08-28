"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  Bot,
  Clock,
  Download,
  Gauge,
  Loader2,
  Play,
  Users,
} from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip as RTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { toast } from "sonner";

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogMedia,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { EmptyState } from "@/components/empty-state";
import { api, ApiError } from "@/lib/api";
import { subscribeJob } from "@/lib/ws";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import ArrivalsComparison from "../widgets/ArrivalsComparison";
import CycleTimeComparison from "../widgets/CycleTimeComparison";
import FidelityScorecard from "../widgets/FidelityScorecard";
import { COLORS, LegendDots } from "../widgets/_kit";
import {
  CANONICAL_RESULT_HEADERS,
  resultsKey,
  simJobKey,
  useAgentSimResults,
  useSimulationGate,
  type AgentSimResult,
} from "./queries";

interface RunConfig {
  num_simulations: number;
  central_orchestration: boolean;
  extr_delays: boolean;
  determine_automatically: boolean;
}

type ExportFormat = "csv" | "xes";

/** One simulated run as offered by the download picker. */
type DownloadEntry = { index: number; cases?: number; events?: number };

const EXPORT_FORMATS: { id: ExportFormat; label: string; hint: string }[] = [
  {
    id: "csv",
    label: "CSV",
    hint: "Flat table – opens in Excel and re-imports into Mate.",
  },
  {
    id: "xes",
    label: "XES",
    hint: "IEEE 1849 event log – the standard for process-mining tools.",
  },
];

const DEFAULTS: RunConfig = {
  num_simulations: 5,
  central_orchestration: false,
  extr_delays: false,
  determine_automatically: false,
};

export default function AgentSimulatorPanel({ logId }: { logId: string; moduleId: string }) {
  const qc = useQueryClient();
  const results = useAgentSimResults(logId);
  const data = results.data;
  const ready = data?.status === "ready";

  // ── module config (the only channel for run params into a subprocess job) ──
  const cfgQuery = useQuery<{ config: Partial<RunConfig>; enabled: boolean }>({
    queryKey: ["modules", "agentsimulator", "config"],
    queryFn: () => api(`/api/v1/modules/agentsimulator/config`),
    staleTime: 60_000,
    retry: false,
  });
  const [form, setForm] = useState<RunConfig>(DEFAULTS);
  const seeded = useRef(false);
  useEffect(() => {
    if (!seeded.current && cfgQuery.isSuccess) {
      seeded.current = true;
      setForm({ ...DEFAULTS, ...(cfgQuery.data?.config ?? {}) });
    }
  }, [cfgQuery.isSuccess, cfgQuery.data]);

  // ── run lifecycle ──────────────────────────────────────────────────────
  // Job state comes from the shared gate (a poll of the per-user jobs list),
  // so a run started from a dashboard widget – or another tab – lights this
  // panel up too. The SSE stream is attached on top of whichever job is
  // active for smooth progress + completion toasts.
  const gate = useSimulationGate(logId);
  const activeJob = gate.activeJob;
  // `kicking` covers the config PUT that precedes the enqueue mutation, so a
  // double-click can't start two runs.
  const [kicking, setKicking] = useState(false);
  const running = kicking || gate.starting || activeJob != null;

  const [progress, setProgress] = useState<{ fraction: number; stage: string }>({
    fraction: 0,
    stage: "",
  });
  const subRef = useRef<{ close: () => void } | null>(null);
  const subbedJobId = useRef<string | null>(null);
  useEffect(() => () => subRef.current?.close(), []);

  useEffect(() => {
    const job = activeJob;
    if (!job || subbedJobId.current === job.id) return;
    subbedJobId.current = job.id;
    subRef.current?.close();
    // Seed from the polled row so an externally-started job paints a sensible
    // bar before the first streamed tick arrives.
    const total = job.progress_total ?? 0;
    setProgress({
      fraction: total > 0 ? job.progress_current / total : 0,
      stage: job.message ?? job.stage ?? "Queued…",
    });

    const finish = (kind: "completed" | "failed" | "cancelled", error?: string) => {
      subRef.current?.close();
      subRef.current = null;
      subbedJobId.current = null;
      if (kind === "completed") {
        toast.success("Simulation complete");
        void qc.invalidateQueries({ queryKey: resultsKey(logId) });
      } else if (kind === "failed") {
        toast.error(`Simulation failed: ${error ?? "unknown error"}`);
      } else {
        toast("Simulation cancelled");
      }
      // Flip the gate promptly instead of waiting for the next poll tick.
      void qc.invalidateQueries({ queryKey: simJobKey(logId) });
    };

    subRef.current = subscribeJob(job.id, (env) => {
      const p = env.payload as Record<string, unknown>;
      if (env.topic === "job.progress") {
        const cur = typeof p.current === "number" ? p.current : 0;
        const tot = typeof p.total === "number" && p.total > 0 ? p.total : 100;
        setProgress({ fraction: cur / tot, stage: String(p.message ?? p.stage ?? "") });
      } else if (env.topic === "job.snapshot") {
        // Attached (or reconnected) after the job already ended.
        const st = String(p.status ?? "");
        if (st === "completed" || st === "failed" || st === "cancelled") {
          finish(st, typeof p.error === "string" ? p.error : undefined);
        }
      } else if (env.topic === "job.completed") {
        finish("completed");
      } else if (env.topic === "job.failed") {
        finish("failed", String(p.error ?? p.message ?? "unknown error"));
      } else if (env.topic === "job.cancelled") {
        finish("cancelled");
      }
    });
  }, [activeJob, logId, qc]);

  async function run() {
    if (running) return;
    setKicking(true);
    try {
      // Persist the form first – ctx.config is the only channel for run
      // params into a subprocess job – then enqueue via the shared gate.
      await api(`/api/v1/modules/agentsimulator/config`, {
        method: "PUT",
        json: { config: form, enabled: true },
      });
      await gate.startAsync();
    } catch (e) {
      toast.error(
        e instanceof ApiError ? `Could not start: ${String(e.detail)}` : "Could not start simulation",
      );
    } finally {
      setKicking(false);
    }
  }

  // "Re-run to apply": the options form differs from the params of the run
  // whose results are on screen.
  const lastParams = data?.params;
  const configChanged =
    ready &&
    lastParams != null &&
    (form.num_simulations !== lastParams.num_simulations ||
      form.central_orchestration !== lastParams.central_orchestration ||
      form.extr_delays !== lastParams.extr_delays ||
      form.determine_automatically !== lastParams.determine_automatically);

  async function download(index: number, format: ExportFormat = "csv") {
    try {
      // The two query params a subprocess route forwards to its handler are the
      // stub's `args`/`kwargs`: the run index travels as `?args=<i>` and the
      // export format as `?kwargs=<fmt>` (see module._parse_log_index /
      // _parse_format).
      const r = await api<{ status: string; content?: string; filename?: string }>(
        `/api/v1/modules/agentsimulator/simulated-log?log_id=${encodeURIComponent(logId)}&args=${index}&kwargs=${format}`,
        { headers: CANONICAL_RESULT_HEADERS },
      );
      if (r.status !== "ready" || !r.content) {
        toast("No simulated log to download yet.");
        return;
      }
      const mime = format === "xes" ? "application/xml" : "text/csv";
      const url = URL.createObjectURL(new Blob([r.content], { type: mime }));
      const a = document.createElement("a");
      a.href = url;
      a.download = r.filename ?? `simulated_log_${index + 1}.${format}`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      toast.error("Download failed.");
    }
  }

  return (
    <div className="space-y-6">
      {/* Controls */}
      <Card>
        <CardContent className="space-y-4">
          <div className="flex items-start justify-between gap-4">
            <div className="flex items-start gap-2">
              <Bot className="mt-0.5 h-5 w-5 text-primary" />
              <div>
                <h3 className="text-sm font-semibold">Agent-based simulation</h3>
                <p className="max-w-prose text-xs text-muted-foreground">
                  Learns agent behaviour, calendars and handovers from this log, splits it into
                  train/test, and generates synthetic logs scored against the held-out test split.
                </p>
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              {configChanged && !running && (
                <Badge
                  variant="outline"
                  className="border-amber-400/60 text-amber-600 dark:text-amber-400"
                  title="The options below differ from the run shown – Re-run to apply them"
                >
                  Settings changed
                </Badge>
              )}
              <Button onClick={run} disabled={running} size="sm">
                {running ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Play className="h-4 w-4" />
                )}
                {running ? "Running…" : ready ? "Re-run" : "Run simulation"}
              </Button>
            </div>
          </div>

          <Separator />

          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <Label className="text-xs">Simulated logs</Label>
                <span className="text-xs font-medium tabular-nums">{form.num_simulations}</span>
              </div>
              <Slider
                min={1}
                max={10}
                step={1}
                value={[form.num_simulations]}
                onValueChange={([v]) => setForm((f) => ({ ...f, num_simulations: v }))}
                disabled={running}
              />
              <p className="text-[11px] text-muted-foreground">
                Repetitions of the same model – more runs give a stabler fidelity score.
              </p>
            </div>
            <div className="space-y-3">
              <ToggleRow
                label="Determine configuration automatically"
                hint="Pick handover type & delays by trial runs. Most accurate, much slower."
                checked={form.determine_automatically}
                onChange={(v) => setForm((f) => ({ ...f, determine_automatically: v }))}
                disabled={running}
              />
              <ToggleRow
                label="Central orchestration"
                hint="Assign work centrally instead of autonomous handovers."
                checked={form.central_orchestration}
                onChange={(v) => setForm((f) => ({ ...f, central_orchestration: v }))}
                disabled={running || form.determine_automatically}
              />
              <ToggleRow
                label="Discover extraneous delays"
                hint="Model waiting time not explained by resource availability. Slower."
                checked={form.extr_delays}
                onChange={(v) => setForm((f) => ({ ...f, extr_delays: v }))}
                disabled={running || form.determine_automatically}
              />
            </div>
          </div>

          {running && <ProgressBar fraction={progress.fraction} stage={progress.stage} />}
          {!running && (
            <p className="text-[11px] text-muted-foreground">
              Discovery + simulation can take several minutes; progress streams live above the
              dock.
            </p>
          )}
        </CardContent>
      </Card>

      {results.isLoading ? (
        <Skeleton className="h-96 w-full" />
      ) : ready && data ? (
        <Results logId={logId} data={data} onDownload={download} />
      ) : running ? (
        <div className="rounded-md border border-dashed border-border bg-card/40 p-8 text-center text-sm text-muted-foreground">
          <Loader2 className="mx-auto mb-2 h-5 w-5 animate-spin text-primary" />
          Simulation running – results will appear here when it finishes.
        </div>
      ) : results.isFetching ? (
        // A run just finished and the results are being refetched – bridge the
        // gap instead of flashing the empty-state CTA.
        <Skeleton className="h-96 w-full" />
      ) : (
        <EmptyState
          icon={Bot}
          title="No simulation yet"
          description="AgentSimulator learns agent behaviour, working calendars and handovers from this log, then generates synthetic runs and scores how closely they match the real process."
          primaryAction={
            <Button onClick={run}>
              <Play className="h-4 w-4" /> Run simulation
            </Button>
          }
          className="py-10"
        />
      )}
    </div>
  );
}

function Results({
  logId,
  data,
  onDownload,
}: {
  logId: string;
  data: AgentSimResult;
  onDownload: (index: number, format: ExportFormat) => void;
}) {
  // The run whose format picker is open; `null` = dialog closed.
  const [picking, setPicking] = useState<DownloadEntry | null>(null);
  // Caches written before per-run downloads carry no `downloads` list but do
  // hold run 0 under the legacy cache key - offer that single download.
  const downloads: DownloadEntry[] = data.downloads?.length ? data.downloads : [{ index: 0 }];
  return (
    <div className="space-y-6">
      {/* Summary + fidelity */}
      <Card>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-sm font-semibold">Simulation fidelity</h3>
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge variant="secondary">{data.params?.mode}</Badge>
              <Badge variant="outline">{data.simulation?.num_logs ?? 0} logs</Badge>
              {data.runtime_seconds != null && (
                <Badge variant="outline">{data.runtime_seconds}s</Badge>
              )}
            </div>
          </div>
          <p className="text-xs text-muted-foreground">
            Distance of each simulated log to the real test log, averaged over the runs (± std).
            Lower is better; useful above all for comparing configurations.
          </p>
          <div className="min-h-[7rem]">
            <FidelityScorecard logId={logId} />
          </div>
          <SummaryStats data={data} />
        </CardContent>
      </Card>

      {/* Distributions */}
      <Card>
        <CardContent className="space-y-3">
          <h3 className="text-sm font-semibold">Real vs simulated distributions</h3>
          <Tabs defaultValue="cycle">
            <TabsList className="flex flex-wrap">
              <TabsTrigger value="cycle">Cycle time</TabsTrigger>
              <TabsTrigger value="arrivals">Arrivals</TabsTrigger>
              <TabsTrigger value="activities">Activity mix</TabsTrigger>
              <TabsTrigger value="circadian">Time of day</TabsTrigger>
              <TabsTrigger value="handover">Handovers</TabsTrigger>
            </TabsList>
            <TabsContent value="cycle">
              <ChartBox caption={cycleCaption(data)}>
                <CycleTimeComparison logId={logId} />
              </ChartBox>
            </TabsContent>
            <TabsContent value="arrivals">
              <ChartBox caption="New cases started over elapsed time (each log from its own start).">
                <ArrivalsComparison logId={logId} />
              </ChartBox>
            </TabsContent>
            <TabsContent value="activities">
              <ChartBox caption="Activity execution counts (per-run average for simulated).">
                <ActivityMix data={data} />
              </ChartBox>
            </TabsContent>
            <TabsContent value="circadian">
              <ChartBox caption="Events by hour of day – does the simulator respect working hours?">
                <Circadian data={data} />
              </ChartBox>
            </TabsContent>
            <TabsContent value="handover">
              <HandoverPanels data={data} />
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>

      {/* Preview + downloads */}
      <Card>
        <CardContent className="space-y-3">
          <div>
            <h3 className="text-sm font-semibold">
              Simulated {downloads.length === 1 ? "log" : `logs (${downloads.length})`}
            </h3>
            <p className="text-[11px] text-muted-foreground">
              Each run is one synthetic log generated by the same model; pick CSV or XES when
              downloading.
            </p>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {downloads.map((d) => (
              <Button key={d.index} onClick={() => setPicking(d)} size="sm" variant="outline">
                <Download className="h-4 w-4" />
                Run {d.index + 1}
                {d.cases != null && (
                  <span className="text-muted-foreground">· {d.cases} cases</span>
                )}
              </Button>
            ))}
          </div>
          <PreviewTable data={data} />
        </CardContent>
      </Card>

      <DownloadFormatDialog
        run={picking}
        onClose={() => setPicking(null)}
        onPick={(index, format) => {
          setPicking(null);
          onDownload(index, format);
        }}
      />
    </div>
  );
}

/** Format picker shown when a run's download button is clicked. */
function DownloadFormatDialog({
  run,
  onClose,
  onPick,
}: {
  run: DownloadEntry | null;
  onClose: () => void;
  onPick: (index: number, format: ExportFormat) => void;
}) {
  // Radix unmounts the content on close, so the label/counts are read from the
  // last non-null run to avoid text flicker during the exit animation.
  const shown = useRef<DownloadEntry | null>(null);
  if (run) shown.current = run;
  const entry = run ?? shown.current;

  return (
    <AlertDialog open={run != null} onOpenChange={(open) => !open && onClose()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogMedia>
            <Download />
          </AlertDialogMedia>
          <AlertDialogTitle>Download run {(entry?.index ?? 0) + 1}</AlertDialogTitle>
          <AlertDialogDescription>
            {entry?.cases != null
              ? `${entry.cases} cases${entry.events != null ? `, ${entry.events} events` : ""} – choose a format.`
              : "Choose a format for the simulated log."}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="grid gap-2 sm:grid-cols-2">
          {EXPORT_FORMATS.map((f) => (
            <button
              key={f.id}
              type="button"
              onClick={() => entry && onPick(entry.index, f.id)}
              className="rounded-md border border-border/60 bg-card/40 p-3 text-left transition-colors hover:border-primary hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
            >
              <span className="flex items-center gap-1.5 text-sm font-medium">
                <Download className="h-4 w-4 text-primary" />
                {f.label}
              </span>
              <span className="mt-1 block text-xs text-muted-foreground">{f.hint}</span>
            </button>
          ))}
        </div>
        <AlertDialogFooter>
          <AlertDialogCancel size="sm">Cancel</AlertDialogCancel>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function SummaryStats({ data }: { data: AgentSimResult }) {
  const items: { icon: typeof Clock; label: string; value: string }[] = [
    {
      icon: Users,
      label: "Test cases",
      value: `${data.test?.cases ?? "–"} (${data.test?.events ?? "–"} events)`,
    },
    {
      icon: Gauge,
      label: "Sim cases / run",
      value: `${data.simulation?.avg_cases ?? "–"}`,
    },
    {
      icon: Clock,
      label: "Real cycle (median)",
      value: `${data.cycle_time?.real_stats.median_h ?? "–"}h`,
    },
    {
      icon: Clock,
      label: "Sim cycle (median)",
      value: `${data.cycle_time?.sim_stats.median_h ?? "–"}h`,
    },
  ];
  return (
    <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
      {items.map((it, i) => (
        <div key={i} className="rounded-md border border-border/60 bg-muted/20 px-3 py-2">
          <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wide text-muted-foreground">
            <it.icon className="h-3 w-3" /> {it.label}
          </div>
          <div className="mt-0.5 truncate text-sm font-semibold tabular-nums">{it.value}</div>
        </div>
      ))}
    </div>
  );
}

function ChartBox({ caption, children }: { caption?: string; children: ReactNode }) {
  return (
    <div className="space-y-2 pt-3">
      <div className="h-72 w-full">{children}</div>
      {caption && <p className="text-[11px] text-muted-foreground">{caption}</p>}
    </div>
  );
}

function ActivityMix({ data }: { data: AgentSimResult }) {
  const rows = data.activities ?? [];
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart
        layout="vertical"
        data={rows}
        margin={{ left: 8, right: 16, top: 4, bottom: 4 }}
      >
        <CartesianGrid strokeDasharray="3 3" className="stroke-border/40" horizontal={false} />
        <XAxis
          type="number"
          tick={{ fontSize: 10 }}
          stroke="currentColor"
          className="text-muted-foreground"
        />
        <YAxis
          type="category"
          dataKey="activity"
          width={120}
          tick={{ fontSize: 10 }}
          stroke="currentColor"
          className="text-muted-foreground"
        />
        <RTooltip contentStyle={{ fontSize: 12 }} cursor={{ fillOpacity: 0.08 }} />
        <Bar dataKey="real" name="Real" fill={COLORS.real} radius={[0, 2, 2, 0]} />
        <Bar dataKey="sim" name="Simulated" fill={COLORS.sim} radius={[0, 2, 2, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

function Circadian({ data }: { data: AgentSimResult }) {
  const rows = data.circadian ?? [];
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={rows} margin={{ left: 0, right: 8, top: 4, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" className="stroke-border/40" vertical={false} />
        <XAxis
          dataKey="hour"
          tick={{ fontSize: 10 }}
          stroke="currentColor"
          className="text-muted-foreground"
          tickFormatter={(v) => `${v}h`}
        />
        <YAxis
          tick={{ fontSize: 10 }}
          stroke="currentColor"
          className="text-muted-foreground"
          allowDecimals={false}
        />
        <RTooltip contentStyle={{ fontSize: 12 }} cursor={{ fillOpacity: 0.08 }} />
        <Bar dataKey="real" name="Real" fill={COLORS.real} radius={[2, 2, 0, 0]} />
        <Bar dataKey="sim" name="Simulated" fill={COLORS.sim} radius={[2, 2, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

function HandoverPanels({ data }: { data: AgentSimResult }) {
  const h = data.handover;
  if (!h || h.resources.length === 0) {
    return <p className="pt-3 text-xs text-muted-foreground">No handover data.</p>;
  }
  return (
    <div className="space-y-2 pt-3">
      <p className="text-[11px] text-muted-foreground">
        Resource → resource handover probability (top {h.resources.length}). A faithful agent model
        reproduces who passes work to whom.
      </p>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <HandoverMatrix title="Real (test)" resources={h.resources} matrix={h.real} color={COLORS.real} />
        <HandoverMatrix title="Simulated" resources={h.resources} matrix={h.sim} color={COLORS.sim} />
      </div>
    </div>
  );
}

function HandoverMatrix({
  title,
  resources,
  matrix,
  color,
}: {
  title: string;
  resources: string[];
  matrix: number[][];
  color: string;
}) {
  const short = (r: string) => (r.length > 10 ? `${r.slice(0, 9)}…` : r);
  return (
    <div className="space-y-1">
      <div className="text-xs font-medium">{title}</div>
      <div className="overflow-x-auto">
        <table className="border-collapse text-[10px]">
          <tbody>
            {matrix.map((row, i) => (
              <tr key={i}>
                <td className="pr-1 text-right text-muted-foreground" title={resources[i]}>
                  {short(resources[i])}
                </td>
                {row.map((v, j) => (
                  <td
                    key={j}
                    className="h-6 w-6 border border-border/30 text-center tabular-nums"
                    style={{ background: v ? withAlpha(color, 0.12 + 0.7 * v) : undefined }}
                    title={`${resources[i]} → ${resources[j]}: ${v}`}
                  >
                    {v ? Math.round(v * 100) : ""}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function PreviewTable({ data }: { data: AgentSimResult }) {
  const pv = data.preview;
  if (!pv || pv.rows.length === 0) {
    return <p className="text-xs text-muted-foreground">No rows to preview.</p>;
  }
  return (
    <div className="space-y-2">
      <div className="max-h-72 overflow-auto rounded-md border border-border/60">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-muted/40">
            <tr>
              {pv.columns.map((c) => (
                <th key={c} className="px-2 py-1 text-left font-medium">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {pv.rows.map((row, i) => (
              <tr key={i} className="border-t border-border/40">
                {row.map((cell, j) => (
                  <td key={j} className="px-2 py-1 tabular-nums">
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-[11px] text-muted-foreground">
        Showing {pv.rows.length} of {pv.total} events from run 1.
      </p>
    </div>
  );
}

function ToggleRow({
  label,
  hint,
  checked,
  onChange,
  disabled,
}: {
  label: string;
  hint: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0">
        <Label className="text-xs">{label}</Label>
        <p className="text-[11px] text-muted-foreground">{hint}</p>
      </div>
      <Switch checked={checked} onCheckedChange={onChange} disabled={disabled} />
    </div>
  );
}

function ProgressBar({ fraction, stage }: { fraction: number; stage: string }) {
  return (
    <div className="space-y-1">
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
        <div
          className="h-full rounded-full bg-primary transition-all"
          style={{ width: `${Math.round(Math.min(Math.max(fraction, 0), 1) * 100)}%` }}
        />
      </div>
      <p className="text-xs text-muted-foreground">{stage || "Running…"}</p>
    </div>
  );
}

function cycleCaption(data: AgentSimResult): string {
  const r = data.cycle_time?.real_stats;
  const s = data.cycle_time?.sim_stats;
  if (!r || !s) return "Case-duration distribution.";
  return `Case duration – real median ${r.median_h}h / p90 ${r.p90_h}h · simulated median ${s.median_h}h / p90 ${s.p90_h}h.`;
}

function withAlpha(hex: string, alpha: number): string {
  const a = Math.round(Math.min(Math.max(alpha, 0), 1) * 255)
    .toString(16)
    .padStart(2, "0");
  return `${hex}${a}`;
}
