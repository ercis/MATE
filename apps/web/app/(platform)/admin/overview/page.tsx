"use client";

import { useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  Activity,
  Briefcase,
  CalendarDays,
  Clock,
  Database,
  FileStack,
  Globe,
  MousePointerClick,
  ShieldAlert,
  Users,
} from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { rawFetch } from "@/lib/api";
import type {
  JobsInsights,
  StorageInsights,
  UsageInsights,
  UsersInsights,
} from "@/lib/api-types";
import { formatNumber } from "@/lib/format";
import { Skeleton } from "@/components/ui/skeleton";
import { StatCardsSkeleton, ChartCardSkeleton } from "@/components/skeletons";

interface DayCount {
  day: string;
  count: number;
}
interface LabelCount {
  label: string;
  count: number;
}
interface TopUser {
  user_id: string;
  email: string | null;
  username: string | null;
  count: number;
}
interface Kpis {
  user_count: number;
  log_count: number;
  events_ingested: number;
  cases_total: number;
  analytics_events: number;
  sessions_total: number;
  active_users_30d: number;
  events_per_session: number;
  bounce_rate_pct: number;
  avg_session_seconds: number;
}
interface Overview {
  days: number;
  kpis: Kpis;
  signups_by_day: DayCount[];
  logs_by_day: DayCount[];
  logs_by_status: LabelCount[];
  logs_by_format: LabelCount[];
  logs_by_model: LabelCount[];
  top_users: TopUser[];
  jobs_by_status: LabelCount[];
  job_failures_by_day: DayCount[];
  sessions_by_day: DayCount[];
  top_event_types: LabelCount[];
  top_paths: LabelCount[];
  activity_by_hour: LabelCount[];
  activity_by_weekday: LabelCount[];
}

const RANGES = [
  { value: 30, label: "30 days" },
  { value: 90, label: "90 days" },
  { value: 365, label: "12 months" },
];

/** "2026-06-17" → "06-17" for compact day-series axis ticks. */
function shortDay(day: string): string {
  return day.length >= 10 ? day.slice(5) : day;
}

/** Seconds → compact "Xh Ym" / "Xm Ys" / "Xs" for the avg-session KPI. */
function formatDuration(seconds: number): string {
  if (seconds <= 0) return "–";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.round(seconds % 60);
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

export default function AdminOverviewPage() {
  const [data, setData] = useState<Overview | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "forbidden" | "error">("loading");
  const [days, setDays] = useState(90);

  useEffect(() => {
    let cancelled = false;
    setState("loading");
    void (async () => {
      try {
        const res = await rawFetch(`/api/v1/admin/insights/overview?days=${days}`);
        if (res.status === 403) {
          if (!cancelled) setState("forbidden");
          return;
        }
        if (!res.ok) throw new Error(String(res.status));
        const json = (await res.json()) as Overview;
        if (!cancelled) {
          setData(json);
          setState("ready");
        }
      } catch {
        if (!cancelled) setState("error");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [days]);

  return (
    <div className="space-y-4">
      {state === "forbidden" ? (
        <div className="flex items-start gap-2 rounded-md border border-border bg-muted/40 p-3 text-xs text-muted-foreground">
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            This dashboard requires the <code>admin</code> role. Ask an
            administrator to grant it in Keycloak (Realm roles → admin).
          </span>
        </div>
      ) : state === "error" ? (
        <p className="text-xs text-destructive">Failed to load admin overview.</p>
      ) : state === "loading" || data === null ? (
        <div className="space-y-4">
          <div className="flex items-center justify-end">
            <Skeleton className="h-7 w-28 rounded-md" />
          </div>
          <StatCardsSkeleton />
          <div className="grid gap-4 lg:grid-cols-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <ChartCardSkeleton key={i} />
            ))}
          </div>
        </div>
      ) : (
        <>
          <div className="flex items-center justify-end gap-2">
            <label className="text-xs text-muted-foreground" htmlFor="range">
              Range
            </label>
            <select
              id="range"
              value={days}
              onChange={(e) => setDays(Number(e.target.value))}
              className="cursor-pointer rounded-md border border-border bg-surface px-2 py-1 text-xs"
            >
              {RANGES.map((r) => (
                <option key={r.value} value={r.value}>
                  {r.label}
                </option>
              ))}
            </select>
          </div>

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            <Kpi label="Users" value={data.kpis.user_count} />
            <Kpi label="Event logs" value={data.kpis.log_count} />
            <Kpi label="Events ingested" value={data.kpis.events_ingested} />
            <Kpi label="Cases" value={data.kpis.cases_total} />
            <Kpi label="Active users (30d)" value={data.kpis.active_users_30d} />
            <Kpi label="Sessions" value={data.kpis.sessions_total} />
            <Kpi label="Events / session" value={data.kpis.events_per_session} />
            <Kpi label="Bounce rate" value={`${data.kpis.bounce_rate_pct}%`} />
            <Kpi label="Avg. session" value={formatDuration(data.kpis.avg_session_seconds)} />
          </div>

          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            <ChartCard title="New users" icon={Users} empty={data.signups_by_day.length === 0}>
              <DayLineChart data={data.signups_by_day} />
            </ChartCard>
            <ChartCard title="Logs imported" icon={FileStack} empty={data.logs_by_day.length === 0}>
              <DayLineChart data={data.logs_by_day} />
            </ChartCard>

            <ChartCard
              title="Logs by status"
              icon={Database}
              empty={data.logs_by_status.length === 0}
            >
              <LabelBarChart data={data.logs_by_status} />
            </ChartCard>
            <ChartCard
              title="Logs by source format"
              icon={Database}
              empty={data.logs_by_format.length === 0}
            >
              <LabelBarChart data={data.logs_by_format} />
            </ChartCard>

            <ChartCard
              title="Top users by log count"
              icon={Users}
              empty={data.top_users.length === 0}
            >
              <LabelBarChart
                data={data.top_users.map((u) => ({
                  label: u.username || u.email || u.user_id.slice(0, 8),
                  count: u.count,
                }))}
                horizontal
              />
            </ChartCard>
            <ChartCard
              title="Jobs by status"
              icon={Briefcase}
              empty={data.jobs_by_status.length === 0}
            >
              <LabelBarChart data={data.jobs_by_status} />
            </ChartCard>

            <ChartCard
              title="Failed jobs"
              icon={Briefcase}
              empty={data.job_failures_by_day.length === 0}
            >
              <DayLineChart data={data.job_failures_by_day} />
            </ChartCard>
            <ChartCard
              title="Sessions"
              icon={Activity}
              empty={data.sessions_by_day.length === 0}
            >
              <DayLineChart data={data.sessions_by_day} />
            </ChartCard>

            <ChartCard
              title="Top usage events"
              icon={MousePointerClick}
              empty={data.top_event_types.length === 0}
            >
              <LabelBarChart data={data.top_event_types} horizontal />
            </ChartCard>

            <ChartCard title="Top pages" icon={Globe} empty={data.top_paths.length === 0}>
              <LabelBarChart data={data.top_paths} horizontal />
            </ChartCard>
            <ChartCard
              title="Activity by hour (UTC)"
              icon={Clock}
              empty={data.activity_by_hour.length === 0}
            >
              <LabelBarChart data={data.activity_by_hour} />
            </ChartCard>
            <ChartCard
              title="Activity by weekday"
              icon={CalendarDays}
              empty={data.activity_by_weekday.every((d) => d.count === 0)}
            >
              <LabelBarChart data={data.activity_by_weekday} />
            </ChartCard>
          </div>

          <UsersSection days={days} />
          <StorageSection days={days} />
          <JobsSection days={days} />
          <UsageSection days={days} />
        </>
      )}
    </div>
  );
}

/** Fetch one admin-insights metric group, mirroring the page's rawFetch + 403
 *  handling (the page itself only renders these once the overview loaded, i.e.
 *  the caller is already an admin). */
function useInsight<T>(path: string, days: number): T | null {
  const [data, setData] = useState<T | null>(null);
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const res = await rawFetch(`${path}?days=${days}`);
        if (!res.ok) return;
        const json = (await res.json()) as T;
        if (!cancelled) setData(json);
      } catch {
        /* surfaced by the overview-level error state */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [path, days]);
  return data;
}

function SectionHeading({ children }: { children: React.ReactNode }) {
  return <h2 className="pt-2 text-sm font-semibold">{children}</h2>;
}

function UsersSection({ days }: { days: number }) {
  const data = useInsight<UsersInsights>("/api/v1/admin/insights/users", days);
  if (!data) return null;
  return (
    <>
      <SectionHeading>User activity</SectionHeading>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Kpi label="Users" value={data.user_count} />
        <Kpi label="Active (range)" value={data.active_users_in_range} />
        <Kpi label="Onboarded" value={data.onboarding_completed} />
        <Kpi label="Onboard %" value={`${data.onboarding_completion_pct}%`} />
      </div>
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <ChartCard title="Active users / day" icon={Users} empty={data.active_users_by_day.length === 0}>
          <DayLineChart data={data.active_users_by_day} />
        </ChartCard>
        <ChartCard title="Last seen" icon={Clock} empty={data.last_seen_buckets.every((b) => b.count === 0)}>
          <LabelBarChart data={data.last_seen_buckets.map((b) => ({ label: b.bucket, count: b.count }))} />
        </ChartCard>
        <ChartCard
          title="Top users by events"
          icon={Users}
          empty={data.top_users_by_events.length === 0}
        >
          <LabelBarChart
            data={data.top_users_by_events.map((u) => ({
              label: u.username || u.email || u.user_id.slice(0, 8),
              count: u.count,
            }))}
            horizontal
          />
        </ChartCard>
        <ChartCard title="Sessions / day" icon={Activity} empty={data.sessions_by_day.length === 0}>
          <DayLineChart data={data.sessions_by_day} />
        </ChartCard>
      </div>
    </>
  );
}

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

function StorageSection({ days }: { days: number }) {
  const data = useInsight<StorageInsights>("/api/v1/admin/insights/storage", days);
  if (!data) return null;
  return (
    <>
      <SectionHeading>Storage &amp; data</SectionHeading>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Kpi label="Backend" value={data.backend_mode} />
        <Kpi label="Total logs" value={data.total_logs} />
        <Kpi label="Total events" value={data.total_events} />
        <Kpi
          label={data.backend_mode === "s3" ? "S3 used" : "S3"}
          value={data.backend_mode === "s3" ? formatBytes(data.s3_used_bytes) : "–"}
        />
      </div>
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <ChartCard title="Events by user" icon={Users} empty={data.per_user.length === 0}>
          <LabelBarChart
            data={data.per_user.map((u) => ({
              label: u.username || u.email || u.user_id.slice(0, 8),
              count: u.events_total,
            }))}
            horizontal
          />
        </ChartCard>
        <ChartCard title="Largest logs" icon={FileStack} empty={data.largest_logs.length === 0}>
          <LabelBarChart
            data={data.largest_logs.map((l) => ({ label: l.name, count: l.events_count ?? 0 }))}
            horizontal
          />
        </ChartCard>
      </div>
    </>
  );
}

function JobsSection({ days }: { days: number }) {
  const data = useInsight<JobsInsights>("/api/v1/admin/insights/jobs", days);
  if (!data) return null;
  return (
    <>
      <SectionHeading>Jobs &amp; system health</SectionHeading>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Kpi label="Concurrency" value={data.runtime.concurrency} />
        <Kpi label="Live workers" value={data.runtime.live_workers} />
        <Kpi label="Queue depth" value={data.runtime.queue_depth} />
        <Kpi label="Running" value={data.runtime.running} />
        <Kpi label="Avg. runtime" value={formatDuration(data.avg_duration_seconds)} />
        <Kpi label="Slowest" value={formatDuration(data.slowest_seconds)} />
      </div>
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <ChartCard title="Jobs by type" icon={Briefcase} empty={data.by_type.length === 0}>
          <LabelBarChart data={data.by_type} horizontal />
        </ChartCard>
        <ChartCard title="Jobs by status" icon={Briefcase} empty={data.by_status.length === 0}>
          <LabelBarChart data={data.by_status} />
        </ChartCard>
        <ChartCard title="Completions / day" icon={Activity} empty={data.completions_by_day.length === 0}>
          <DayLineChart data={data.completions_by_day} />
        </ChartCard>
        <ChartCard title="Failures / day" icon={Briefcase} empty={data.failures_by_day.length === 0}>
          <DayLineChart data={data.failures_by_day} />
        </ChartCard>
        <ChartCard
          title="Avg. runtime / day"
          icon={Clock}
          empty={data.avg_duration_by_day.length === 0}
        >
          <DayLineChart
            data={data.avg_duration_by_day}
            valueFormat={(s) => (s > 0 ? formatDuration(s) : "0s")}
          />
        </ChartCard>
      </div>
    </>
  );
}

function UsageSection({ days }: { days: number }) {
  const data = useInsight<UsageInsights>("/api/v1/admin/insights/usage", days);
  if (!data) return null;
  return (
    <>
      <SectionHeading>Module &amp; AI usage</SectionHeading>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <Kpi label="AI chat requests" value={data.ai.chat_requests} />
        <Kpi label="AI guidance" value={data.ai.guidance_requests} />
        <Kpi label="Tokens / cost" value={data.ai.tokens_tracked ? "tracked" : "not tracked"} />
      </div>
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <ChartCard title="Installs by module" icon={Database} empty={data.installs_by_module.length === 0}>
          <LabelBarChart data={data.installs_by_module} horizontal />
        </ChartCard>
        <ChartCard
          title="Runs by module"
          icon={Briefcase}
          empty={data.modules.every((m) => m.runs === 0)}
        >
          <LabelBarChart
            data={data.modules.map((m) => ({ label: m.module_id, count: m.runs }))}
            horizontal
          />
        </ChartCard>
      </div>
    </>
  );
}

function Kpi({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-md border border-border bg-card px-3 py-2">
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="text-xl font-semibold">
        {typeof value === "number" ? formatNumber(value) : value}
      </div>
    </div>
  );
}

function ChartCard({
  title,
  icon: Icon,
  empty,
  children,
}: {
  title: string;
  icon: React.ComponentType<{ className?: string }>;
  empty: boolean;
  children: React.ReactNode;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm">
          <Icon className="h-4 w-4" />
          {title}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {empty ? (
          <p className="py-12 text-center text-xs text-muted-foreground">No data yet.</p>
        ) : (
          <div className="h-48 w-full">{children}</div>
        )}
      </CardContent>
    </Card>
  );
}

function DayLineChart({
  data,
  valueFormat,
}: {
  data: DayCount[];
  valueFormat?: (value: number) => string;
}) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <XAxis
          dataKey="day"
          tickFormatter={shortDay}
          tick={{ fontSize: 10 }}
          interval="preserveStartEnd"
          minTickGap={24}
        />
        <YAxis
          tick={{ fontSize: 10 }}
          allowDecimals={false}
          width={valueFormat ? 48 : 28}
          tickFormatter={valueFormat ? (v) => valueFormat(Number(v)) : undefined}
        />
        <Tooltip
          contentStyle={{ fontSize: 12 }}
          formatter={valueFormat ? (v) => valueFormat(Number(v)) : undefined}
        />
        <Line
          type="monotone"
          dataKey="count"
          stroke="currentColor"
          className="text-primary"
          dot={false}
          strokeWidth={2}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}

function LabelBarChart({ data, horizontal }: { data: LabelCount[]; horizontal?: boolean }) {
  if (horizontal) {
    return (
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <XAxis type="number" tick={{ fontSize: 10 }} allowDecimals={false} />
          <YAxis
            type="category"
            dataKey="label"
            tick={{ fontSize: 10 }}
            width={96}
            interval={0}
          />
          <Tooltip contentStyle={{ fontSize: 12 }} cursor={{ fill: "transparent" }} />
          <Bar dataKey="count" fill="currentColor" className="text-primary" radius={[0, 2, 2, 0]} />
        </BarChart>
      </ResponsiveContainer>
    );
  }
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <XAxis dataKey="label" tick={{ fontSize: 10 }} interval={0} />
        <YAxis tick={{ fontSize: 10 }} allowDecimals={false} width={28} />
        <Tooltip contentStyle={{ fontSize: 12 }} cursor={{ fill: "transparent" }} />
        <Bar dataKey="count" fill="currentColor" className="text-primary" radius={[2, 2, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}
