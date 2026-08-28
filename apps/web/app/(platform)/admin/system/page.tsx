"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { Activity, Cpu, MemoryStick, Server, ShieldAlert } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ChartCardSkeleton } from "@/components/skeletons";
import { rawFetch } from "@/lib/api";
import type { ResourceBreakdownSlice, SystemResources } from "@/lib/api-types";

// recharts-backed charts live in ./system-charts; load them in an async chunk so
// this route's First Load JS stays small. ssr:false because they only render
// client-side from data polled in the browser via rawFetch.
const TimeAreaChart = dynamic(
  () => import("./system-charts").then((m) => m.TimeAreaChart),
  { ssr: false, loading: () => <ChartCardSkeleton /> },
);
const BreakdownDonut = dynamic(
  () => import("./system-charts").then((m) => m.BreakdownDonut),
  { ssr: false, loading: () => <ChartCardSkeleton /> },
);

const POLL_MS = 2000;

const CHART_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-4)",
  "var(--chart-5)",
  "var(--chart-3)",
];

function formatBytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return "–";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function formatPct(n: number): string {
  return `${n.toFixed(n >= 10 ? 0 : 1)}%`;
}

/** Deterministic per-slice colours: idle/free capacity renders blank (light
 * track), the "System / other" remainder is a mid grey, and every
 * measured/estimated slice cycles the chart palette in order. */
function breakdownColors(slices: ResourceBreakdownSlice[]): string[] {
  let i = 0;
  return slices.map((s) => {
    if (s.source === "idle") return "var(--muted)";
    if (s.source === "system") return "var(--muted-foreground)";
    const c = CHART_COLORS[i % CHART_COLORS.length];
    i++;
    return c;
  });
}

export default function AdminSystemPage() {
  const [data, setData] = useState<SystemResources | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "forbidden" | "error">("loading");
  const inFlight = useRef(false);

  const load = useCallback(async (silent: boolean) => {
    if (inFlight.current) return;
    inFlight.current = true;
    if (!silent) setState((s) => (s === "forbidden" ? s : "loading"));
    try {
      const res = await rawFetch("/api/v1/system/resources");
      if (res.status === 403) {
        setState("forbidden");
        return;
      }
      if (!res.ok) throw new Error(String(res.status));
      const json = (await res.json()) as SystemResources;
      setData(json);
      setState("ready");
    } catch {
      if (!silent) setState("error");
    } finally {
      inFlight.current = false;
    }
  }, []);

  // Initial load (with loading state)…
  useEffect(() => {
    void load(false);
  }, [load]);
  // …then poll quietly so the graphs stay live without a flicker.
  useEffect(() => {
    const t = setInterval(() => void load(true), POLL_MS);
    return () => clearInterval(t);
  }, [load]);

  const windowLabel = data ? Math.round(data.history_window_seconds / 60) : 3;

  return (
    <div className="space-y-4">
      {state === "forbidden" ? (
        <div className="flex items-start gap-2 rounded-md border border-border bg-muted/40 p-3 text-xs text-muted-foreground">
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            This view requires the <code>admin</code> role. Ask an administrator to grant it in
            Keycloak (Realm roles → admin).
          </span>
        </div>
      ) : state === "error" ? (
        <div className="rounded-md border border-border px-3 py-8 text-center text-xs text-destructive">
          Failed to load system metrics.
        </div>
      ) : !data ? (
        <div className="space-y-3">
          <Skeleton className="h-3 w-80" />
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            <ChartCardSkeleton />
            <ChartCardSkeleton />
          </div>
          <ChartCardSkeleton />
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            <ChartCardSkeleton />
            <ChartCardSkeleton />
          </div>
        </div>
      ) : (
        <>
          <p className="text-xs text-muted-foreground">
            Live host CPU &amp; memory and where the load is coming from. Auto-refreshes every{" "}
            {POLL_MS / 1000}s; the graphs show the last {windowLabel} min.
          </p>

          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            <CpuCard data={data} />
            <MemoryCard data={data} />
          </div>

          <CoreCard data={data} />

          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            <BreakdownCard
              title="CPU by source"
              slices={data.cpu_breakdown}
              format={(v) => formatPct(v)}
            />
            <BreakdownCard
              title="Memory by source"
              slices={data.memory_breakdown}
              format={(v) => formatBytes(v)}
            />
          </div>

          <RunningJobs data={data} />
        </>
      )}
    </div>
  );
}

function Readout({ value, sub }: { value: string; sub: string }) {
  return (
    <div className="flex items-baseline gap-2">
      <span className="text-2xl font-semibold tabular-nums">{value}</span>
      <span className="text-xs text-muted-foreground">{sub}</span>
    </div>
  );
}

function CpuCard({ data }: { data: SystemResources }) {
  const { cpu, history } = data;
  return (
    <Card>
      <CardHeader className="gap-1">
        <CardTitle className="flex items-center gap-2 text-sm">
          <Cpu className="h-4 w-4" /> CPU
        </CardTitle>
        <Readout value={formatPct(cpu.current_pct)} sub={`${cpu.cores_logical} threads`} />
      </CardHeader>
      <CardContent>
        <div className="h-44 w-full">
          <TimeAreaChart
            history={history}
            dataKey="cpu_pct"
            yMax={100}
            yTick={(v) => `${v}%`}
            tipFormat={(v) => formatPct(v)}
          />
        </div>
      </CardContent>
    </Card>
  );
}

function MemoryCard({ data }: { data: SystemResources }) {
  const { memory, history } = data;
  return (
    <Card>
      <CardHeader className="gap-1">
        <CardTitle className="flex items-center gap-2 text-sm">
          <MemoryStick className="h-4 w-4" /> Memory
        </CardTitle>
        <Readout
          value={`${formatBytes(memory.used_bytes)} / ${formatBytes(memory.total_bytes)}`}
          sub={formatPct(memory.current_pct)}
        />
      </CardHeader>
      <CardContent>
        <div className="h-44 w-full">
          <TimeAreaChart
            history={history}
            dataKey="mem_used_bytes"
            yMax={memory.total_bytes}
            yTick={(v) => formatBytes(v)}
            tipFormat={(v) => formatBytes(v)}
          />
        </div>
      </CardContent>
    </Card>
  );
}

function CoreCard({ data }: { data: SystemResources }) {
  const { cpu } = data;
  return (
    <Card>
      <CardHeader className="gap-1">
        <CardTitle className="flex items-center gap-2 text-sm">
          <Activity className="h-4 w-4" /> CPU by core / thread
        </CardTitle>
        <p className="text-xs text-muted-foreground">
          {cpu.cores_logical} logical threads · {cpu.cores_physical} physical cores. Bar = current
          load; the tick marks the running max.
        </p>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-1 gap-x-6 gap-y-1.5 sm:grid-cols-2">
          {cpu.per_core.map((c) => (
            <div key={c.index} className="flex items-center gap-2 text-xs">
              <span className="w-10 shrink-0 tabular-nums text-muted-foreground">#{c.index}</span>
              <div className="relative h-2 flex-1 overflow-hidden rounded-full bg-muted">
                <div
                  className="absolute inset-y-0 left-0 rounded-full bg-primary"
                  style={{ width: `${Math.min(100, c.current_pct)}%` }}
                />
                <div
                  className="absolute inset-y-0 w-0.5 bg-foreground/50"
                  style={{ left: `calc(${Math.min(100, c.max_pct)}% - 1px)` }}
                />
              </div>
              <span className="w-10 shrink-0 text-right tabular-nums">
                {formatPct(c.current_pct)}
              </span>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function BreakdownCard({
  title,
  slices,
  format,
}: {
  title: string;
  slices: ResourceBreakdownSlice[];
  format: (v: number) => string;
}) {
  const colors = breakdownColors(slices);
  const hasData = slices.some((s) => s.value > 0);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm">
          <Server className="h-4 w-4" /> {title}
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="h-44 w-full sm:w-44">
          {hasData ? (
            <BreakdownDonut slices={slices} colors={colors} format={format} />
          ) : (
            <p className="py-16 text-center text-xs text-muted-foreground">No load.</p>
          )}
        </div>
        <ul className="flex-1 space-y-1 text-xs">
          {slices.map((s, i) => (
            <li key={`${s.source}-${s.label}`} className="flex items-center gap-2">
              <span
                className="h-2.5 w-2.5 shrink-0 rounded-full"
                style={{ background: colors[i] }}
              />
              <span className="min-w-0 flex-1 truncate">
                {s.label}
                {s.estimated ? <span className="text-muted-foreground"> (est.)</span> : null}
              </span>
              <span className="shrink-0 tabular-nums text-muted-foreground">
                {format(s.value)}
              </span>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function RunningJobs({ data }: { data: SystemResources }) {
  const jobs = data.running_jobs;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Running jobs ({jobs.length})</CardTitle>
      </CardHeader>
      <CardContent>
        {jobs.length === 0 ? (
          <p className="text-xs text-muted-foreground">No jobs running right now.</p>
        ) : (
          <div className="divide-y divide-border text-xs">
            {jobs.map((j) => (
              <div key={j.id} className="flex items-center gap-3 py-1.5">
                <span className="shrink-0 rounded bg-primary/10 px-1.5 py-0.5 font-medium text-primary">
                  {j.module_id ?? j.type}
                </span>
                <span className="min-w-0 flex-1 truncate">{j.title}</span>
                <span className="shrink-0 tabular-nums text-muted-foreground">
                  {j.user_id.slice(0, 8)}…
                </span>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
