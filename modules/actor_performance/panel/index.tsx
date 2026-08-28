"use client";

/**
 * Actor Performance panel - three states:
 *
 * 1. Sidecar unreachable  -> setup card (compose profile / docker run one-liner);
 *    previous results, if cached, stay visible below.
 * 2. Reachable, no result -> behavior-class explainer + Run.
 * 3. Result               -> stat tiles, log-wide behavior mix (share + mean wait
 *    per class), and the per-transition decomposition table with expandable rows.
 *
 * Behavior-class colors are fixed identity hues (never re-assigned by rank),
 * validated for light and dark surfaces separately; the dark set is applied via
 * `.dark` CSS-variable overrides so theme switches need no JS. Classes are always
 * ALSO named in text (legend, cards, table) - color never carries identity alone.
 *
 * Run params travel through module config (PUT /config) because subprocess jobs
 * read ctx.config - and PUT replaces the stored dict, so the panel merges the
 * fetched config before writing (never wipe bolt_uri/password set on the
 * settings page).
 */

import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  ChevronDown,
  Clock,
  Database,
  Loader2,
  Play,
  RefreshCw,
  Users,
  Workflow,
} from "lucide-react";
import { toast } from "sonner";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api, ApiError } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDuration, formatNumber, formatRelative } from "@/lib/format";
import { subscribeJob } from "@/lib/ws";

// ── behavior classes (fixed order = stacked-segment order) ──────────────────

const CLASSES = [
  {
    key: "continuation",
    label: "Continuation",
    desc: "Same actor continued directly with the next step.",
  },
  {
    key: "interruption",
    label: "Interruption",
    desc: "Same actor did both steps but worked on something else in between.",
  },
  {
    key: "handover_idle",
    label: "Handover (idle)",
    desc: "Another actor took over after having been idle (or with no earlier work).",
  },
  {
    key: "handover_prioritized",
    label: "Handover (prioritized)",
    desc: "Another actor squeezed the step in while mid another task.",
  },
  {
    key: "handover_deprioritized",
    label: "Handover (deprioritized)",
    desc: "Another actor finished other work first before taking the step.",
  },
  {
    key: "unclassified",
    label: "Unclassified",
    desc: "Transitions the classification did not match (should be rare).",
  },
] as const;

type ClassKey = (typeof CLASSES)[number]["key"];

/** Light + dark palettes validated separately (dataviz six-checks); unclassified
 * is a reserved neutral outside the categorical set. */
const PALETTE_CSS = `
.ap-scope {
  --ap-continuation: #6366f1;
  --ap-interruption: #f59e0b;
  --ap-handover_idle: #10b981;
  --ap-handover_prioritized: #0ea5e9;
  --ap-handover_deprioritized: #ec4899;
  --ap-unclassified: #94a3b8;
}
.dark .ap-scope {
  --ap-interruption: #d97706;
  --ap-handover_idle: #059669;
  --ap-handover_prioritized: #0284c7;
}
`;

const classVar = (key: ClassKey) => `var(--ap-${key})`;

// ── result types (mirror module.py RESULT_SCHEMA = 1) ───────────────────────

interface ClassStats {
  count: number;
  percentage: number;
  mean_hours: number;
  median_hours?: number;
  p90_hours?: number;
}

interface EdgeRow {
  source_activity: string;
  source_lifecycle: string;
  sink_activity: string;
  sink_lifecycle: string;
  count: number;
  behaviors: Record<string, ClassStats>;
}

interface ApResult {
  status: string;
  generated_at?: string;
  runtime_seconds?: number;
  params?: { edge_min_freq: number; max_edges: number };
  input?: {
    events: number;
    cases: number;
    resources: number;
    dropped_null_resource: number;
    has_lifecycle: boolean;
  };
  graph?: { task_instances: number; df_case_edges: number };
  behavior_totals?: Record<string, ClassStats>;
  edges?: EdgeRow[];
  truncated?: boolean;
}

interface ApHealth {
  status: string;
  uri: string;
  import_dir: string;
  hint: string | null;
}

const hours = (h: number | undefined) => formatDuration(h != null ? h * 3600 : null);
const pct = (p: number | undefined) => `${((p ?? 0) * 100).toFixed(1)}%`;

// ── panel ────────────────────────────────────────────────────────────────────

export default function ActorPerformancePanel({ logId }: { logId: string; moduleId: string }) {
  const qc = useQueryClient();

  const health = useQuery<ApHealth>({
    queryKey: ["actor_performance", "health"],
    queryFn: () => api(`/api/v1/modules/actor_performance/health`),
    refetchInterval: (q) => (q.state.data?.status === "ok" ? false : 8000),
    retry: false,
  });
  const healthy = health.data?.status === "ok";

  const results = useQuery<ApResult>({
    queryKey: ["actor_performance", "results", logId],
    queryFn: () =>
      api(`/api/v1/modules/actor_performance/results?log_id=${encodeURIComponent(logId)}`),
  });
  const data = results.data;
  const ready = data?.status === "ready";

  const cfg = useQuery<{ config: Record<string, unknown>; enabled: boolean }>({
    queryKey: ["actor_performance", "config"],
    queryFn: () => api(`/api/v1/modules/actor_performance/config`),
    staleTime: 60_000,
    retry: false,
  });
  const [minFreq, setMinFreq] = useState(0);
  const [maxEdges, setMaxEdges] = useState(150);
  const seeded = useRef(false);
  useEffect(() => {
    if (!seeded.current && cfg.isSuccess) {
      seeded.current = true;
      const c = cfg.data?.config ?? {};
      setMinFreq(Number(c.edge_min_freq ?? 0) || 0);
      setMaxEdges(Number(c.max_edges ?? 150) || 150);
    }
  }, [cfg.isSuccess, cfg.data]);

  // ── run lifecycle (panel-local; the job dock shows it globally too) ────────
  const [kicking, setKicking] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [progress, setProgress] = useState<{ fraction: number | null; stage: string }>({
    fraction: null,
    stage: "",
  });
  const subRef = useRef<{ close: () => void } | null>(null);
  useEffect(() => () => subRef.current?.close(), []);
  const running = kicking || jobId != null;

  async function run() {
    if (running) return;
    setKicking(true);
    try {
      // Merge over the fetched config - PUT replaces the whole dict and the
      // connection fields (bolt_uri/password/...) must survive.
      const existing = cfg.data?.config ?? {};
      await api(`/api/v1/modules/actor_performance/config`, {
        method: "PUT",
        json: {
          config: { ...existing, edge_min_freq: minFreq, max_edges: maxEdges },
          enabled: cfg.data?.enabled ?? true,
        },
      });
      const r = await api<{ job_id: string }>(
        `/api/v1/modules/actor_performance/run?log_id=${encodeURIComponent(logId)}`,
        { method: "POST" },
      );
      setJobId(r.job_id);
      setProgress({ fraction: 0, stage: "Queued…" });
      subRef.current?.close();
      subRef.current = subscribeJob(r.job_id, (env) => {
        const p = env.payload as Record<string, unknown>;
        const finish = (kind: "completed" | "failed" | "cancelled", error?: string) => {
          subRef.current?.close();
          subRef.current = null;
          setJobId(null);
          if (kind === "completed") {
            toast.success("Decomposition complete");
            void qc.invalidateQueries({ queryKey: ["actor_performance", "results", logId] });
          } else if (kind === "failed") {
            toast.error(`Analysis failed: ${error ?? "unknown error"}`);
            void health.refetch();
          } else {
            toast("Analysis cancelled");
          }
        };
        if (env.topic === "job.progress") {
          const cur = typeof p.current === "number" ? p.current : 0;
          const tot = typeof p.total === "number" && p.total > 0 ? p.total : 100;
          setProgress({ fraction: cur / tot, stage: String(p.message ?? p.stage ?? "") });
        } else if (env.topic === "job.snapshot") {
          const st = String(p.status ?? "");
          if (st === "completed" || st === "failed" || st === "cancelled") {
            finish(st as "completed" | "failed" | "cancelled", String(p.error ?? "") || undefined);
          }
        } else if (env.topic === "job.completed") {
          finish("completed");
        } else if (env.topic === "job.failed") {
          finish("failed", String(p.error ?? p.message ?? "unknown error"));
        } else if (env.topic === "job.cancelled") {
          finish("cancelled");
        }
      });
    } catch (e) {
      toast.error(
        e instanceof ApiError ? `Could not start: ${String(e.detail)}` : "Could not start analysis",
      );
    } finally {
      setKicking(false);
    }
  }

  const configChanged =
    ready &&
    data?.params != null &&
    (minFreq !== data.params.edge_min_freq || maxEdges !== data.params.max_edges);

  return (
    <div className="ap-scope space-y-4">
      <style>{PALETTE_CSS}</style>

      {/* Controls */}
      <Card>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="flex items-start gap-2">
              <Workflow className="mt-0.5 h-5 w-5 text-primary" />
              <div>
                <h3 className="text-sm font-semibold">Waiting time by actor behavior</h3>
                <p className="max-w-prose text-xs text-muted-foreground">
                  Builds an Event Knowledge Graph of this log in the Neo4j sidecar, classifies
                  every case transition by how the work moved between actors, and decomposes
                  waiting times per transition (Klijn et&nbsp;al., ICPM&nbsp;2024).
                </p>
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              {configChanged && !running && (
                <Badge
                  variant="outline"
                  className="border-amber-400/60 text-amber-600 dark:text-amber-400"
                  title="The options differ from the run shown - Re-run to apply them"
                >
                  Re-run to apply
                </Badge>
              )}
              <Button size="sm" onClick={run} disabled={running || health.isLoading || !healthy}>
                {running ? (
                  <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                ) : ready ? (
                  <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
                ) : (
                  <Play className="mr-1.5 h-3.5 w-3.5" />
                )}
                {running ? "Running…" : ready ? "Re-run" : "Run analysis"}
              </Button>
            </div>
          </div>

          <div className="flex flex-wrap items-end gap-4 text-xs">
            <label className="flex flex-col gap-1">
              <span className="text-muted-foreground">Min. transition frequency</span>
              <input
                type="number"
                min={0}
                value={minFreq}
                onChange={(e) => setMinFreq(Math.max(0, Number(e.target.value) || 0))}
                className="h-8 w-32 rounded-md border bg-background px-2 text-xs"
                disabled={running}
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-muted-foreground">Max. transitions</span>
              <input
                type="number"
                min={10}
                max={1000}
                value={maxEdges}
                onChange={(e) => setMaxEdges(Math.max(10, Math.min(1000, Number(e.target.value) || 150)))}
                className="h-8 w-32 rounded-md border bg-background px-2 text-xs"
                disabled={running}
              />
            </label>
            {ready && data?.generated_at && (
              <span className="ml-auto text-muted-foreground">
                Last run {formatRelative(data.generated_at)}
                {data.runtime_seconds != null && <> · {formatDuration(data.runtime_seconds)}</>}
              </span>
            )}
          </div>

          {running && (
            <div className="space-y-1.5">
              <div className="flex items-center justify-between text-xs text-muted-foreground">
                <span className="inline-flex items-center gap-1.5">
                  <Loader2 className="h-3 w-3 animate-spin text-primary" />
                  {progress.stage || "Working…"}
                </span>
                {progress.fraction != null && <span>{Math.round(progress.fraction * 100)}%</span>}
              </div>
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                {progress.fraction == null ? (
                  <div className="h-full w-full animate-pulse rounded-full bg-primary/40" />
                ) : (
                  <div
                    className="h-full rounded-full bg-primary transition-all"
                    style={{ width: `${Math.max(progress.fraction * 100, 2)}%` }}
                  />
                )}
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Setup card when the sidecar is down */}
      {!health.isLoading && !healthy && <SetupCard health={health.data} />}

      {/* Results / empty state */}
      {results.isLoading ? (
        <Skeleton className="h-64 w-full" />
      ) : ready && data ? (
        <ResultsView data={data} />
      ) : healthy && !running ? (
        <ExplainerCard />
      ) : null}
    </div>
  );
}

// ── setup / empty states ─────────────────────────────────────────────────────

function SetupCard({ health }: { health: ApHealth | undefined }) {
  const authFailed = health?.status === "auth-failed";
  return (
    <Card>
      <CardContent className="space-y-3">
        <div className="flex items-start gap-2">
          <Database className="mt-0.5 h-5 w-5 text-amber-500" />
          <div className="space-y-1">
            <h3 className="text-sm font-semibold">
              {authFailed ? "Neo4j rejected the credentials" : "Graph sidecar not running"}
            </h3>
            <p className="max-w-prose text-xs text-muted-foreground">
              {health?.hint ??
                "This module needs the optional Neo4j sidecar - a local graph engine used as per-run scratch space."}
            </p>
          </div>
        </div>
        {!authFailed && (
          <div className="space-y-2 text-xs">
            <p className="text-muted-foreground">
              Docker stack: set{" "}
              <code className="rounded bg-muted px-1 py-0.5">COMPOSE_PROFILES=graph</code> in{" "}
              <code className="rounded bg-muted px-1 py-0.5">.env</code> and re-run compose (see
              DEPLOY.md §4b). Host-mode dev: start it once by hand -
            </p>
            <pre className="overflow-x-auto rounded-md bg-muted p-3 text-[11px] leading-relaxed">
              {`docker run -d --name mate-neo4j \\
  -p 127.0.0.1:7687:7687 \\
  -e NEO4J_AUTH=neo4j/mate-graph-dev \\
  -e NEO4J_PLUGINS='["apoc"]' \\
  -e APOC_IMPORT_FILE_ENABLED=true \\
  -e NEO4J_apoc_import_file_enabled=true \\
  -e NEO4J_server_memory_heap_max__size=2G \\
  -v "$(pwd)/data/neo4j-import":/var/lib/neo4j/import \\
  neo4j:5.26-community`}
            </pre>
            <p className="text-muted-foreground">
              Checked {health?.uri ?? "bolt://localhost:7687"} - this card refreshes automatically.
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function ExplainerCard() {
  return (
    <Card>
      <CardContent className="space-y-3">
        <h3 className="text-sm font-semibold">What the classes mean</h3>
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {CLASSES.filter((c) => c.key !== "unclassified").map((c) => (
            <div key={c.key} className="flex items-start gap-2 rounded-md border p-2.5">
              <span
                className="mt-1 h-2.5 w-2.5 shrink-0 rounded-full"
                style={{ background: classVar(c.key) }}
              />
              <div>
                <div className="text-xs font-medium">{c.label}</div>
                <div className="text-xs text-muted-foreground">{c.desc}</div>
              </div>
            </div>
          ))}
        </div>
        <p className="text-xs text-muted-foreground">
          Run the analysis to see how much waiting time each behavior contributes on this log.
          The graph is wiped after every run - nothing is stored in the sidecar.
        </p>
      </CardContent>
    </Card>
  );
}

// ── results ──────────────────────────────────────────────────────────────────

function ResultsView({ data }: { data: ApResult }) {
  const totals = data.behavior_totals ?? {};
  const edges = data.edges ?? [];

  return (
    <>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <StatTile icon={Database} label="Events" value={formatNumber(data.input?.events)} />
        <StatTile icon={Workflow} label="Cases" value={formatNumber(data.input?.cases)} />
        <StatTile icon={Users} label="Actors" value={formatNumber(data.input?.resources)} />
        <StatTile
          icon={Workflow}
          label="Task instances"
          value={formatNumber(data.graph?.task_instances)}
        />
        <StatTile
          icon={Workflow}
          label="Transitions"
          value={formatNumber(edges.length)}
          badge={data.truncated ? "capped" : undefined}
        />
        <StatTile icon={Clock} label="Runtime" value={formatDuration(data.runtime_seconds)} />
      </div>

      {(data.input?.dropped_null_resource ?? 0) > 0 && (
        <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <AlertTriangle className="h-3.5 w-3.5 text-amber-500" />
          {formatNumber(data.input?.dropped_null_resource)} events without a resource were
          excluded - the analysis needs to know who performed each step.
        </p>
      )}

      <BehaviorMixCard totals={totals} />
      <TransitionsCard edges={edges} truncated={Boolean(data.truncated)} />
    </>
  );
}

function StatTile({
  icon: Icon,
  label,
  value,
  badge,
}: {
  icon: typeof Database;
  label: string;
  value: string;
  badge?: string;
}) {
  return (
    <Card>
      <CardContent className="flex flex-col gap-1 p-3">
        <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
          <Icon className="h-3.5 w-3.5" />
          {label}
          {badge && (
            <Badge variant="outline" className="px-1 py-0 text-[10px]">
              {badge}
            </Badge>
          )}
        </span>
        <span className="text-lg font-semibold tabular-nums">{value}</span>
      </CardContent>
    </Card>
  );
}

/** One 100% stacked bar (2px gaps) + per-class stat cards. Identity is carried
 * by label text everywhere; color is reinforcement. */
function BehaviorMixCard({ totals }: { totals: Record<string, ClassStats> }) {
  const present = CLASSES.filter((c) => (totals[c.key]?.count ?? 0) > 0);
  const maxMean = Math.max(...present.map((c) => totals[c.key]?.mean_hours ?? 0), 0);

  return (
    <Card>
      <CardContent className="space-y-4">
        <div>
          <h3 className="text-sm font-semibold">Behavior mix across the log</h3>
          <p className="text-xs text-muted-foreground">
            Share of case transitions per behavior, and the mean wait each one carries.
          </p>
        </div>

        <div className="flex h-3 w-full overflow-hidden rounded-full">
          {present.map((c) => (
            <div
              key={c.key}
              title={`${c.label}: ${pct(totals[c.key]?.percentage)} of transitions`}
              style={{
                width: `${Math.max((totals[c.key]?.percentage ?? 0) * 100, 0.5)}%`,
                background: classVar(c.key),
                marginRight: 2,
              }}
            />
          ))}
        </div>

        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {present.map((c) => {
            const s = totals[c.key];
            return (
              <div key={c.key} className="space-y-1.5 rounded-md border p-2.5" title={c.desc}>
                <div className="flex items-center justify-between gap-2">
                  <span className="inline-flex min-w-0 items-center gap-1.5 text-xs font-medium">
                    <span
                      className="h-2.5 w-2.5 shrink-0 rounded-full"
                      style={{ background: classVar(c.key) }}
                    />
                    <span className="truncate">{c.label}</span>
                  </span>
                  <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                    {formatNumber(s?.count)} · {pct(s?.percentage)}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <div className="h-1.5 grow overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full rounded-full"
                      style={{
                        width: `${maxMean > 0 ? ((s?.mean_hours ?? 0) / maxMean) * 100 : 0}%`,
                        background: classVar(c.key),
                      }}
                    />
                  </div>
                  <span className="shrink-0 text-xs tabular-nums">
                    {hours(s?.mean_hours)} <span className="text-muted-foreground">mean wait</span>
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}

const SHOW_LIMIT = 150;

function TransitionsCard({ edges, truncated }: { edges: EdgeRow[]; truncated: boolean }) {
  const [filter, setFilter] = useState("");
  const [sort, setSort] = useState<"count" | "mean">("count");
  const [open, setOpen] = useState<string | null>(null);

  const rows = useMemo(() => {
    const q = filter.trim().toLowerCase();
    let out = edges;
    if (q) {
      out = out.filter((e) =>
        `${e.source_activity} ${e.sink_activity}`.toLowerCase().includes(q),
      );
    }
    if (sort === "mean") {
      out = [...out].sort(
        (a, b) => (b.behaviors.all?.mean_hours ?? 0) - (a.behaviors.all?.mean_hours ?? 0),
      );
    }
    return out;
  }, [edges, filter, sort]);

  const shown = rows.slice(0, SHOW_LIMIT);

  return (
    <Card>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold">Transitions</h3>
            <p className="text-xs text-muted-foreground">
              Waiting time between consecutive steps of a case, decomposed by behavior. Click a
              row for the full breakdown.
              {truncated && " The run was capped at the most frequent transitions."}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <input
              type="search"
              placeholder="Filter activities…"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              className="h-8 w-48 rounded-md border bg-background px-2 text-xs"
            />
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value === "mean" ? "mean" : "count")}
              className="h-8 rounded-md border bg-background px-2 text-xs"
            >
              <option value="count">Most frequent</option>
              <option value="mean">Slowest (mean)</option>
            </select>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b text-left text-muted-foreground">
                <th className="py-2 pr-2 font-medium">Transition</th>
                <th className="w-20 py-2 pr-2 text-right font-medium">Count</th>
                <th className="w-24 py-2 pr-2 text-right font-medium">Mean wait</th>
                <th className="w-48 py-2 pr-2 font-medium">Behavior mix</th>
                <th className="w-8 py-2" />
              </tr>
            </thead>
            <tbody>
              {shown.map((e) => {
                const key = `${e.source_activity}|${e.source_lifecycle}|${e.sink_activity}|${e.sink_lifecycle}`;
                const isOpen = open === key;
                return (
                  <EdgeTableRow
                    key={key}
                    edge={e}
                    open={isOpen}
                    onToggle={() => setOpen(isOpen ? null : key)}
                  />
                );
              })}
            </tbody>
          </table>
          {rows.length > SHOW_LIMIT && (
            <p className="py-2 text-center text-xs text-muted-foreground">
              Showing {SHOW_LIMIT} of {rows.length} transitions - use the filter to narrow down.
            </p>
          )}
          {rows.length === 0 && (
            <p className="py-6 text-center text-xs text-muted-foreground">
              No transition matches the filter.
            </p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function EdgeTableRow({
  edge,
  open,
  onToggle,
}: {
  edge: EdgeRow;
  open: boolean;
  onToggle: () => void;
}) {
  const all = edge.behaviors.all;
  return (
    <>
      <tr
        className={cn("cursor-pointer border-b transition-colors hover:bg-muted/50", open && "bg-muted/30")}
        onClick={onToggle}
      >
        <td className="py-2 pr-2">
          <span className="font-medium">{edge.source_activity}</span>
          {edge.source_lifecycle && (
            <span className="ml-1 text-muted-foreground">·{edge.source_lifecycle}</span>
          )}
          <span className="mx-1.5 text-muted-foreground">→</span>
          <span className="font-medium">{edge.sink_activity}</span>
          {edge.sink_lifecycle && (
            <span className="ml-1 text-muted-foreground">·{edge.sink_lifecycle}</span>
          )}
        </td>
        <td className="py-2 pr-2 text-right tabular-nums">{formatNumber(edge.count)}</td>
        <td className="py-2 pr-2 text-right tabular-nums">{hours(all?.mean_hours)}</td>
        <td className="py-2 pr-2">
          <div className="flex h-2 w-full overflow-hidden rounded-full">
            {CLASSES.map((c) => {
              const s = edge.behaviors[c.key];
              if (!s || s.count === 0) return null;
              return (
                <div
                  key={c.key}
                  title={`${c.label}: ${pct(s.percentage)} · mean ${hours(s.mean_hours)}`}
                  style={{
                    width: `${Math.max(s.percentage * 100, 1)}%`,
                    background: classVar(c.key),
                    marginRight: 2,
                  }}
                />
              );
            })}
          </div>
        </td>
        <td className="py-2 text-right">
          <ChevronDown
            className={cn("h-3.5 w-3.5 text-muted-foreground transition-transform", open && "rotate-180")}
          />
        </td>
      </tr>
      {open && (
        <tr className="border-b bg-muted/20">
          <td colSpan={5} className="p-3">
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {CLASSES.map((c) => {
                const s = edge.behaviors[c.key];
                if (!s || s.count === 0) return null;
                return (
                  <div key={c.key} className="rounded-md border bg-background p-2.5" title={c.desc}>
                    <div className="flex items-center justify-between gap-2">
                      <span className="inline-flex items-center gap-1.5 text-xs font-medium">
                        <span
                          className="h-2.5 w-2.5 rounded-full"
                          style={{ background: classVar(c.key) }}
                        />
                        {c.label}
                      </span>
                      <span className="text-xs tabular-nums text-muted-foreground">
                        {formatNumber(s.count)} · {pct(s.percentage)}
                      </span>
                    </div>
                    <dl className="mt-1.5 grid grid-cols-3 gap-1 text-xs">
                      <div>
                        <dt className="text-muted-foreground">mean</dt>
                        <dd className="tabular-nums">{hours(s.mean_hours)}</dd>
                      </div>
                      <div>
                        <dt className="text-muted-foreground">median</dt>
                        <dd className="tabular-nums">{hours(s.median_hours)}</dd>
                      </div>
                      <div>
                        <dt className="text-muted-foreground">p90</dt>
                        <dd className="tabular-nums">{hours(s.p90_hours)}</dd>
                      </div>
                    </dl>
                  </div>
                );
              })}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
