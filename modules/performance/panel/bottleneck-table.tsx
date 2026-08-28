"use client";

import Link from "next/link";
import { ArrowUpRight } from "lucide-react";
import { Bar, BarChart, ResponsiveContainer } from "recharts";

import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cn } from "@/lib/cn";
import { activityHref } from "@/lib/dashboards/drill";
import { formatDuration, formatNumber } from "@/lib/format";

import { InfoHint } from "./info-hint";
import type { BottleneckItem } from "./queries";

interface BottleneckTableProps {
  logId: string;
  items: BottleneckItem[];
  selectedActivity: string | null;
  onSelectActivity: (activity: string | null) => void;
}

export function BottleneckTable({
  logId,
  items,
  selectedActivity,
  onSelectActivity,
}: BottleneckTableProps) {
  if (!items.length) {
    return (
      <div className="rounded-xl border bg-card p-8 text-center text-sm text-muted-foreground">
        No bottlenecks detected – cases spend a similar amount of time at every activity, so no
        step stands out as unusually slow.
      </div>
    );
  }
  return (
    <div className="rounded-xl border bg-card">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-12">#</TableHead>
            <TableHead>Activity</TableHead>
            <TableHead className="text-right">
              <span className="inline-flex items-center gap-1">
                Avg time spent
                <InfoHint label="What does average time spent mean?">
                  Average time a case spends at this activity — waiting plus processing —
                  measured from the activity&apos;s event until the case moves on to its next
                  step.
                </InfoHint>
              </span>
            </TableHead>
            <TableHead className="text-right">
              <span className="inline-flex items-center gap-1">
                Frequency
                <InfoHint label="What does frequency mean?">
                  How many times this activity occurred across all cases in the log.
                </InfoHint>
              </span>
            </TableHead>
            <TableHead className="text-right">
              <span className="inline-flex items-center gap-1">
                P90 time spent
                <InfoHint label="What does P90 time spent mean?">
                  90% of visits to this activity take less time than this; only the slowest 10%
                  take longer.
                </InfoHint>
              </span>
            </TableHead>
            <TableHead className="text-right">
              <span className="inline-flex items-center gap-1">
                Share
                <InfoHint label="What does share mean?">
                  This activity&apos;s share of the total time spent across all activities in
                  the process (average time spent × frequency). Big shares are where speeding
                  up pays off most.
                </InfoHint>
              </span>
            </TableHead>
            <TableHead className="w-24">
              <span className="inline-flex items-center gap-1">
                Distribution
                <InfoHint label="What does the distribution show?">
                  The spread of time-spent values at this activity: each bar is a duration
                  range, taller bars mean more occurrences in that range.
                </InfoHint>
              </span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((item) => {
            const isSelected = selectedActivity === item.activity;
            return (
              <TableRow
                key={item.activity}
                className={cn(
                  "group cursor-pointer transition-colors",
                  isSelected && "bg-muted",
                )}
                onClick={() => onSelectActivity(isSelected ? null : item.activity)}
              >
                <TableCell className="font-mono text-xs text-muted-foreground">{item.rank}</TableCell>
                <TableCell className="font-medium">
                  {item.activity}
                  {/* Row click keeps toggling the local selection; this is the
                      jump to the platform's canonical activity view. */}
                  <Link
                    href={activityHref(logId, item.activity)}
                    title="Open activity view"
                    aria-label={`Open activity view for ${item.activity}`}
                    className="ml-1.5 inline-flex align-middle text-muted-foreground opacity-0 transition-opacity hover:text-foreground group-hover:opacity-100"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <ArrowUpRight className="h-3.5 w-3.5" />
                  </Link>
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatDuration(item.avg_sojourn_s)}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatNumber(item.frequency)}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatDuration(item.p90_sojourn_s)}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {(item.share_of_total_time * 100).toFixed(1)}%
                </TableCell>
                <TableCell>
                  <div className="h-6 w-20">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={item.histogram.map((h) => ({ count: h.count }))}>
                        <Bar dataKey="count" fill="var(--chart-1)" radius={1} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
