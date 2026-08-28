"use client";

import { useState } from "react";
import type { ReactNode } from "react";
import Link from "next/link";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip as ChartTooltip,
  XAxis,
  YAxis,
} from "recharts";

import { CHART_CHROME, InfoHint, KpiGrid, KpiTile, seriesColor } from "@/components/dashboards/kit";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Slider } from "@/components/ui/slider";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { activityHref } from "@/lib/dashboards/drill";
import { formatDuration, formatNumber } from "@/lib/format";

import {
  usePerformanceActivities,
  usePerformanceKpis,
  usePerformanceTransitions,
} from "./queries";
import type { ActivityRow, TransitionRow } from "./queries";

// Bars are ranked by total time, so the top of the list is where the process
// actually loses time. More than ~25 rows stops being a ranking and starts
// being the table below it.
const MIN_ROWS = 5;
const MAX_ROWS = 25;
const CHART_HEIGHT = 320;

function pct(share: number | undefined): string {
  return share === undefined ? "-" : `${(share * 100).toFixed(1)}%`;
}

function seconds(value: number | undefined): string {
  return value === undefined ? "-" : formatDuration(value);
}

function count(value: number | undefined): string {
  return value === undefined ? "-" : formatNumber(value);
}

export function Panel({ logId }: { logId: string; moduleId: string }) {
  const kpis = usePerformanceKpis(logId);
  const activities = usePerformanceActivities(logId);
  const transitions = usePerformanceTransitions(logId);
  const [topN, setTopN] = useState(10);

  const activityRows = activities.data?.activities ?? [];
  const transitionRows = transitions.data?.transitions ?? [];

  return (
    <div className="space-y-6">
      <KpiStrip loading={kpis.isLoading} error={kpis.isError} data={kpis.data} />

      <Card>
        <CardContent className="space-y-4 pt-6">
          <Tabs defaultValue="activities">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <TabsList>
                <TabsTrigger value="activities">Activities</TabsTrigger>
                <TabsTrigger value="handoffs">Hand-offs</TabsTrigger>
              </TabsList>
              <div className="flex items-center gap-3">
                <span className="text-xs text-muted-foreground">Top {topN}</span>
                {/* Inline width: a `w-[160px]` arbitrary utility used only under
                    modules/ is never emitted by the web Tailwind build. */}
                <div style={{ width: 160 }}>
                  <Slider
                    value={[topN]}
                    min={MIN_ROWS}
                    max={MAX_ROWS}
                    step={5}
                    aria-label="How many rows to rank"
                    onValueChange={(next: number[]) => setTopN(next[0] ?? topN)}
                  />
                </div>
                <InfoHint label="What is dwell time?">
                  Dwell time is the wall-clock gap from an event until the case&apos;s next event -
                  the wait plus the work. A case&apos;s last event has no gap and is left out of the
                  averages.
                </InfoHint>
              </div>
            </div>

            <TabsContent value="activities" className="space-y-4">
              <Section
                loading={activities.isLoading}
                error={activities.isError}
                empty={activityRows.length === 0}
                emptyText="No activity timings for this log."
              >
                <RankChart
                  rows={activityRows.slice(0, topN).map((r) => ({
                    label: r.activity,
                    value: r.total_dwell_seconds ?? 0,
                  }))}
                  valueLabel="Total dwell"
                />
                <ActivityTable logId={logId} rows={activityRows.slice(0, topN)} />
              </Section>
              <Truncation truncated={activities.data?.truncated} shown={activityRows.length} />
            </TabsContent>

            <TabsContent value="handoffs" className="space-y-4">
              <Section
                loading={transitions.isLoading}
                error={transitions.isError}
                empty={transitionRows.length === 0}
                emptyText="No hand-offs for this log."
              >
                <RankChart
                  rows={transitionRows.slice(0, topN).map((r) => ({
                    label: `${r.from_activity} → ${r.to_activity}`,
                    value: r.total_wait_seconds ?? 0,
                  }))}
                  valueLabel="Total wait"
                />
                <TransitionTable logId={logId} rows={transitionRows.slice(0, topN)} />
              </Section>
              <Truncation truncated={transitions.data?.truncated} shown={transitionRows.length} />
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>
    </div>
  );
}

/** Headline figures. Tiles for metrics the log can't support are omitted, not
 * rendered empty - the Java worker leaves those keys out entirely. */
function KpiStrip({
  loading,
  error,
  data,
}: {
  loading: boolean;
  error: boolean;
  data:
    | {
        cases?: number;
        events?: number;
        variants?: number;
        events_per_case?: number;
        cycle_time_avg_seconds?: number;
        cycle_time_median_seconds?: number;
        cycle_time_p90_seconds?: number;
        throughput_cases_per_day?: number;
        processing_time_avg_seconds?: number;
        waiting_time_share?: number;
        resources?: number;
      }
    | undefined;
}) {
  if (loading) {
    return (
      <KpiGrid minTileWidth={170}>
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <Skeleton key={i} className="h-20 w-full" />
        ))}
      </KpiGrid>
    );
  }
  if (error || !data) {
    return <p className="text-sm text-muted-foreground">Could not load performance metrics.</p>;
  }

  return (
    <KpiGrid minTileWidth={170}>
      <KpiTile
        label="Cases"
        value={count(data.cases)}
        hint={data.events !== undefined ? `${formatNumber(data.events)} events` : undefined}
      />
      <KpiTile
        label="Variants"
        value={count(data.variants)}
        hint={
          data.events_per_case !== undefined
            ? `${data.events_per_case.toFixed(1)} events per case`
            : undefined
        }
        info="Distinct paths cases take through the process. Many variants means little standardisation."
      />
      <KpiTile
        label="Avg cycle time"
        value={seconds(data.cycle_time_avg_seconds)}
        hint="first → last event"
        info="Wall-clock duration of a case, averaged. It includes waiting, so a few very slow cases pull it up."
      />
      <KpiTile
        label="Median cycle time"
        value={seconds(data.cycle_time_median_seconds)}
        hint="half of cases are faster"
      />
      <KpiTile
        label="P90 cycle time"
        value={seconds(data.cycle_time_p90_seconds)}
        hint="the slowest 10% take longer"
      />
      <KpiTile
        label="Throughput"
        value={
          data.throughput_cases_per_day === undefined
            ? "-"
            : `${data.throughput_cases_per_day.toFixed(1)} / day`
        }
        hint="cases handled per day"
        info="Cases divided by the calendar span of the log - the volume the process sustained, not its speed."
      />
      {data.processing_time_avg_seconds !== undefined && (
        <KpiTile
          label="Avg processing time"
          value={seconds(data.processing_time_avg_seconds)}
          hint="work per case"
          info="Sum of the events' own durations (start → end_timestamp) per case. Only available when the log records an end timestamp."
        />
      )}
      {data.waiting_time_share !== undefined && (
        <KpiTile
          label="Waiting share"
          value={pct(data.waiting_time_share)}
          hint="of cycle time"
          info="How much of a case's duration is neither work nor recorded - queueing between steps."
        />
      )}
      {data.resources !== undefined && (
        <KpiTile label="Resources" value={count(data.resources)} hint="distinct actors" />
      )}
    </KpiGrid>
  );
}

function Section({
  loading,
  error,
  empty,
  emptyText,
  children,
}: {
  loading: boolean;
  error: boolean;
  empty: boolean;
  emptyText: string;
  children: ReactNode;
}) {
  if (loading) return <Skeleton className="w-full" style={{ height: CHART_HEIGHT }} />;
  if (error) return <p className="text-sm text-muted-foreground">Could not load this ranking.</p>;
  if (empty) return <p className="text-sm text-muted-foreground">{emptyText}</p>;
  return <div className="space-y-4">{children}</div>;
}

/** One series, one colour, ranked bars - the length already encodes the value,
 * so hue stays free (kit convention, same as the dashboard cards). */
function RankChart({
  rows,
  valueLabel,
}: {
  rows: { label: string; value: number }[];
  valueLabel: string;
}) {
  return (
    <div style={{ height: CHART_HEIGHT }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart layout="vertical" data={rows} margin={{ left: 8, right: 24, top: 4, bottom: 4 }}>
          <CartesianGrid stroke={CHART_CHROME.grid} horizontal={false} />
          <XAxis
            type="number"
            tickFormatter={(v: number) => formatDuration(v)}
            tick={{ fontSize: 10 }}
            stroke="currentColor"
            className="text-muted-foreground"
          />
          <YAxis
            type="category"
            dataKey="label"
            width={170}
            tick={{ fontSize: 10 }}
            stroke="currentColor"
            className="text-muted-foreground"
          />
          <ChartTooltip
            // recharts types the value as `ValueType` (string | number | array);
            // the series is seconds, so narrow it here rather than at the axis.
            formatter={(value) =>
              [formatDuration(Number(value)), valueLabel] as [string, string]
            }
            contentStyle={{ fontSize: 12 }}
          />
          <Bar dataKey="value" radius={[0, 3, 3, 0]} fill={seriesColor(0)} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function ActivityTable({ logId, rows }: { logId: string; rows: ActivityRow[] }) {
  const withProcessing = rows.some((r) => r.avg_processing_seconds !== undefined);
  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Activity</TableHead>
            <TableHead className="text-right">Occurrences</TableHead>
            <TableHead className="text-right">Cases</TableHead>
            <TableHead className="text-right">Median dwell</TableHead>
            <TableHead className="text-right">P90 dwell</TableHead>
            {withProcessing && <TableHead className="text-right">Avg processing</TableHead>}
            <TableHead className="text-right">Total dwell</TableHead>
            <TableHead className="text-right">Share</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.activity}>
              <TableCell className="max-w-xs truncate font-medium">
                {/* The activity entity view is the canonical place an activity
                    name goes - same target the dashboard cards drill to. */}
                <Link href={activityHref(logId, row.activity)} className="hover:underline">
                  {row.activity}
                </Link>
              </TableCell>
              <TableCell className="text-right tabular-nums">{count(row.occurrences)}</TableCell>
              <TableCell className="text-right tabular-nums">{count(row.cases)}</TableCell>
              <TableCell className="text-right tabular-nums">
                {seconds(row.median_dwell_seconds)}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {seconds(row.p90_dwell_seconds)}
              </TableCell>
              {withProcessing && (
                <TableCell className="text-right tabular-nums">
                  {seconds(row.avg_processing_seconds)}
                </TableCell>
              )}
              <TableCell className="text-right tabular-nums">
                {seconds(row.total_dwell_seconds)}
              </TableCell>
              <TableCell className="text-right tabular-nums">{pct(row.dwell_share)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function TransitionTable({ logId, rows }: { logId: string; rows: TransitionRow[] }) {
  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Hand-off</TableHead>
            <TableHead className="text-right">Occurrences</TableHead>
            <TableHead className="text-right">Cases</TableHead>
            <TableHead className="text-right">Median wait</TableHead>
            <TableHead className="text-right">P90 wait</TableHead>
            <TableHead className="text-right">Total wait</TableHead>
            <TableHead className="text-right">Share</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={`${row.from_activity}→${row.to_activity}`}>
              <TableCell className="max-w-xs truncate font-medium">
                <Link href={activityHref(logId, row.from_activity)} className="hover:underline">
                  {row.from_activity}
                </Link>
                <span className="px-1 text-muted-foreground">→</span>
                <Link href={activityHref(logId, row.to_activity)} className="hover:underline">
                  {row.to_activity}
                </Link>
              </TableCell>
              <TableCell className="text-right tabular-nums">{count(row.occurrences)}</TableCell>
              <TableCell className="text-right tabular-nums">{count(row.cases)}</TableCell>
              <TableCell className="text-right tabular-nums">
                {seconds(row.median_wait_seconds)}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {seconds(row.p90_wait_seconds)}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {seconds(row.total_wait_seconds)}
              </TableCell>
              <TableCell className="text-right tabular-nums">{pct(row.wait_share)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

/** The worker caps its output; say so rather than letting the tail vanish. */
function Truncation({ truncated, shown }: { truncated: boolean | undefined; shown: number }) {
  if (!truncated) return null;
  return (
    <p className="text-xs text-muted-foreground">
      Ranking capped at {formatNumber(shown)} rows - filter the log to see the rest.
    </p>
  );
}

export default Panel;
