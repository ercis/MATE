"use client";

import { useState } from "react";
import { Activity, Clock, Gauge, TrendingUp, Workflow } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDuration, formatNumber } from "@/lib/format";

import { BottleneckTable } from "./bottleneck-table";
import { CycleTimeHistogram } from "./cycle-time-histogram";
import {
  usePerformanceBottlenecks,
  usePerformanceCycleTimeDistribution,
  usePerformanceKpis,
} from "./queries";

export function PerformancePanel({ logId }: { logId: string; moduleId: string }) {
  const kpis = usePerformanceKpis(logId);
  const histo = usePerformanceCycleTimeDistribution(logId);
  const bottlenecks = usePerformanceBottlenecks(logId);

  const [selectedActivity, setSelectedActivity] = useState<string | null>(null);

  const cases = kpis.data?.summary.cases;

  return (
    <div className="space-y-6">
      {/* KPI strip */}
      <div className="grid grid-cols-1 gap-3 md:grid-cols-5">
        <KpiCard
          icon={TrendingUp}
          title="Throughput"
          value={kpis.data ? `${kpis.data.summary.throughput_cases_per_day.toFixed(1)} cases/day` : null}
          loading={kpis.isLoading}
          subline={cases ? `from ${formatNumber(cases)} cases` : undefined}
        />
        <KpiCard
          icon={Clock}
          title="Avg cycle time"
          value={kpis.data ? formatDuration(kpis.data.summary.avg_cycle_time_s) : null}
          loading={kpis.isLoading}
          subline="mean case duration"
        />
        <KpiCard
          icon={Gauge}
          title="Median cycle"
          value={kpis.data ? formatDuration(kpis.data.summary.median_cycle_time_s) : null}
          loading={kpis.isLoading}
          subline="50% of cases"
        />
        <KpiCard
          icon={Activity}
          title="P90 cycle"
          value={kpis.data ? formatDuration(kpis.data.summary.p90_cycle_time_s) : null}
          loading={kpis.isLoading}
          subline="90% of cases under"
        />
        <KpiCard
          icon={Workflow}
          title="Lead time"
          value={kpis.data ? formatDuration(kpis.data.summary.lead_time_s) : null}
          loading={kpis.isLoading}
          subline="first → last event"
        />
      </div>

      {/* Cycle time distribution */}
      <Card>
        <CardContent className="space-y-3">
          <div className="flex items-baseline justify-between gap-3">
            <h3 className="text-sm font-semibold">Cycle time distribution</h3>
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
            <h3 className="text-sm font-semibold">Critical bottlenecks</h3>
            <span className="text-xs text-muted-foreground">
              activities with sojourn ≥ median + 1.5 × IQR
            </span>
          </div>
          {bottlenecks.isLoading ? (
            <Skeleton className="h-48 w-full" />
          ) : bottlenecks.data ? (
            <BottleneckTable
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
}: {
  icon: LucideIcon;
  title: string;
  value: string | null;
  subline?: string;
  loading?: boolean;
}) {
  return (
    <Card>
      <CardContent>
        <div className="flex items-center justify-between gap-2 text-muted-foreground">
          <span className="text-[10px] uppercase tracking-wide">{title}</span>
          <Icon className="h-3.5 w-3.5" />
        </div>
        <div className="mt-1.5 text-2xl font-semibold tabular-nums">
          {loading ? <Skeleton className="h-7 w-24" /> : (value ?? "–")}
        </div>
        {subline && <div className="mt-1 text-[11px] text-muted-foreground">{subline}</div>}
      </CardContent>
    </Card>
  );
}
