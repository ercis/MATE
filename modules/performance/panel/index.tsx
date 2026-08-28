"use client";

import { useState } from "react";
import type { ReactNode } from "react";
import { useSearchParams } from "next/navigation";
import {
  Activity,
  CalendarRange,
  Clock,
  Gauge,
  Layers3,
  Route,
  TrendingUp,
  Workflow,
  Zap,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDateRange, formatDuration, formatNumber } from "@/lib/format";

import { BottleneckTable } from "./bottleneck-table";
import { CycleTimeHistogram } from "./cycle-time-histogram";
import { InfoHint } from "./info-hint";
import {
  useLogSummary,
  usePerformanceBottlenecks,
  usePerformanceCycleTimeDistribution,
  usePerformanceKpis,
} from "./queries";

// Calendar span between the log's first and last event, in seconds.
function spanSeconds(min: string | null, max: string | null): number | null {
  if (!min || !max) return null;
  const a = new Date(min).getTime();
  const b = new Date(max).getTime();
  if (Number.isNaN(a) || Number.isNaN(b)) return null;
  return Math.max(0, (b - a) / 1000);
}

export function PerformancePanel({ logId }: { logId: string; moduleId: string }) {
  const logSummary = useLogSummary(logId);
  const kpis = usePerformanceKpis(logId);
  const histo = usePerformanceCycleTimeDistribution(logId);
  const bottlenecks = usePerformanceBottlenecks(logId);

  const span = logSummary.data
    ? spanSeconds(logSummary.data.date_min, logSummary.data.date_max)
    : null;

  // Preselect from `?activity=` – e.g. the discovery DFG's "view performance
  // metrics" jump passes the clicked activity along.
  const searchParams = useSearchParams();
  const [selectedActivity, setSelectedActivity] = useState<string | null>(
    () => searchParams.get("activity"),
  );

  const cases = kpis.data?.summary.cases;

  return (
    <div className="space-y-6">
      {/* Log summary – process context (cases / events / variants / time span),
          moved here from the process overview so it sits next to the metrics.
          Inline auto-fit columns instead of a `grid-cols-N` utility: module-only
          Tailwind classes aren't reliably emitted by the shared build (e.g. the
          KPI strip's `md:grid-cols-5` silently collapses), so we size tracks in
          CSS to stay independent of the app's generated class set. */}
      <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))" }}>
        <KpiCard
          icon={Layers3}
          title="Cases"
          value={logSummary.data ? formatNumber(logSummary.data.cases_count) : null}
          loading={logSummary.isLoading}
          subline="process instances"
        />
        <KpiCard
          icon={Zap}
          title="Events"
          value={logSummary.data ? formatNumber(logSummary.data.events_count) : null}
          loading={logSummary.isLoading}
          subline="recorded activities"
        />
        <KpiCard
          icon={Route}
          title="Variants"
          value={logSummary.data ? formatNumber(logSummary.data.variants_count) : null}
          loading={logSummary.isLoading}
          subline="distinct paths"
        />
        <KpiCard
          icon={CalendarRange}
          title="Time span"
          value={
            logSummary.data
              ? formatDateRange(logSummary.data.date_min, logSummary.data.date_max)
              : null
          }
          valueClassName="text-base"
          loading={logSummary.isLoading}
          subline={span !== null ? `over ${formatDuration(span)}` : "no timestamps"}
        />
      </div>

      {/* KPI strip – same auto-fit sizing as the log summary above so both rows
          stay responsive without depending on an emitted `grid-cols-5`. */}
      <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))" }}>
        <KpiCard
          icon={TrendingUp}
          title="Throughput"
          value={kpis.data ? `${kpis.data.summary.throughput_cases_per_day.toFixed(1)} cases/day` : null}
          loading={kpis.isLoading}
          subline={cases ? `from ${formatNumber(cases)} cases` : undefined}
          info="How many cases the process handles per day on average, measured over the full time span of this log. Higher means more volume gets through."
        />
        <KpiCard
          icon={Clock}
          title="Avg cycle time"
          value={kpis.data ? formatDuration(kpis.data.summary.avg_cycle_time_s) : null}
          loading={kpis.isLoading}
          subline="average start → finish"
          info="How long a case takes from its first to its last recorded event, averaged across all cases. A few very slow cases can pull this number up."
        />
        <KpiCard
          icon={Gauge}
          title="Median cycle"
          value={kpis.data ? formatDuration(kpis.data.summary.median_cycle_time_s) : null}
          loading={kpis.isLoading}
          subline="half of cases are faster"
          info="Half of all cases finish within this time. Unlike the average, it is barely affected by rare, very slow cases — often the more 'typical' duration."
        />
        <KpiCard
          icon={Activity}
          title="P90 cycle"
          value={kpis.data ? formatDuration(kpis.data.summary.p90_cycle_time_s) : null}
          loading={kpis.isLoading}
          subline="90% of cases under"
          info="90% of cases finish within this time; only the slowest 10% take longer. A realistic 'worst case' for most of your cases."
        />
        <KpiCard
          icon={Workflow}
          title="Lead time"
          value={kpis.data ? formatDuration(kpis.data.summary.lead_time_s) : null}
          loading={kpis.isLoading}
          subline="first → last event"
          info="End-to-end time a case spends in the process, from its first to its last recorded event, averaged across cases — including waiting between steps."
        />
      </div>

      {/* Cycle time distribution */}
      <Card>
        <CardContent className="space-y-3">
          <div className="flex items-baseline justify-between gap-3">
            <div className="flex items-center gap-1.5">
              <h3 className="text-sm font-semibold">Cycle time distribution</h3>
              <InfoHint label="What does the cycle time distribution show?">
                <p>
                  How long cases take from start to finish. Each bar counts the cases that
                  finished within that time range.
                </p>
                <p>
                  Dashed lines: median = half of cases are faster; p90 / p95 = 90% / 95% of
                  cases are faster.
                </p>
              </InfoHint>
            </div>
            {histo.data && (
              <span className="text-xs text-muted-foreground">
                min {formatDuration(histo.data.stats.min_cycle_time_s)} ·
                max {formatDuration(histo.data.stats.max_cycle_time_s)}
              </span>
            )}
          </div>
          {histo.isLoading ? (
            <Skeleton className="h-72 w-full" />
          ) : histo.data ? (
            <CycleTimeHistogram data={histo.data} />
          ) : (
            <div className="text-xs text-muted-foreground">Could not load distribution.</div>
          )}
        </CardContent>
      </Card>

      {/* Bottlenecks */}
      <Card>
        <CardContent className="space-y-3">
          <div className="flex items-baseline justify-between gap-3">
            <div className="flex items-center gap-1.5">
              <h3 className="text-sm font-semibold">Critical bottlenecks</h3>
              <InfoHint label="What counts as a bottleneck?">
                <p>
                  A bottleneck is an activity where cases spend unusually long compared to the
                  rest of the process. We flag activities whose time spent (waiting +
                  processing, until the case moves on) is far above the typical range — a
                  standard outlier rule.
                </p>
                <p className="text-background/70">
                  Technically: average time spent at the activity ≥ median + 1.5 ×
                  interquartile range (IQR) across all activities.
                </p>
              </InfoHint>
            </div>
            <span className="text-xs text-muted-foreground">
              steps where cases spend unusually long
            </span>
          </div>
          {bottlenecks.isLoading ? (
            <Skeleton className="h-48 w-full" />
          ) : bottlenecks.data ? (
            <BottleneckTable
              logId={logId}
              items={bottlenecks.data.items}
              selectedActivity={selectedActivity}
              onSelectActivity={setSelectedActivity}
            />
          ) : (
            <div className="text-xs text-muted-foreground">Could not load bottlenecks.</div>
          )}
        </CardContent>
      </Card>

    </div>
  );
}

function KpiCard({
  icon: Icon,
  title,
  value,
  subline,
  loading,
  info,
  valueClassName,
}: {
  icon: LucideIcon;
  title: string;
  value: string | null;
  subline?: string;
  loading?: boolean;
  info?: ReactNode;
  valueClassName?: string;
}) {
  return (
    <Card>
      <CardContent>
        <div className="flex items-center justify-between gap-2 text-muted-foreground">
          <span className="flex min-w-0 items-center gap-1">
            <span className="truncate text-[10px] uppercase tracking-wide">{title}</span>
            {info && <InfoHint label={`What does ${title} mean?`}>{info}</InfoHint>}
          </span>
          <Icon className="h-3.5 w-3.5" />
        </div>
        <div
          className={`mt-1.5 truncate font-semibold tabular-nums ${valueClassName ?? "text-2xl"}`}
        >
          {loading ? <Skeleton className="h-7 w-24" /> : (value ?? "–")}
        </div>
        {subline && <div className="mt-1 text-[11px] text-muted-foreground">{subline}</div>}
      </CardContent>
    </Card>
  );
}
