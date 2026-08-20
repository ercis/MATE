"use client";

import { Bar, BarChart, ResponsiveContainer } from "recharts";

import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cn } from "@/lib/cn";
import { formatDuration, formatNumber } from "@/lib/format";

import type { BottleneckItem } from "./queries";

interface BottleneckTableProps {
  items: BottleneckItem[];
  selectedActivity: string | null;
  onSelectActivity: (activity: string | null) => void;
}

export function BottleneckTable({ items, selectedActivity, onSelectActivity }: BottleneckTableProps) {
  if (!items.length) {
    return (
      <div className="rounded-xl border bg-card p-8 text-center text-sm text-muted-foreground">
        No bottlenecks detected – sojourn times are evenly distributed across activities.
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
            <TableHead className="text-right">Frequency</TableHead>
            <TableHead className="text-right">Avg sojourn</TableHead>
            <TableHead className="text-right">P90 sojourn</TableHead>
            <TableHead className="text-right">Share</TableHead>
            <TableHead className="w-24">Distribution</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((item) => {
            const isSelected = selectedActivity === item.activity;
            return (
              <TableRow
                key={item.activity}
                className={cn(
                  "cursor-pointer transition-colors",
                  isSelected && "bg-muted",
                )}
                onClick={() => onSelectActivity(isSelected ? null : item.activity)}
              >
                <TableCell className="font-mono text-xs text-muted-foreground">{item.rank}</TableCell>
                <TableCell className="font-medium">{item.activity}</TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatNumber(item.frequency)}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatDuration(item.avg_sojourn_s)}
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
