"use client";

import Link from "next/link";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { VariantRow } from "@/lib/api-types";
import type { ActivityRenameMap } from "@/lib/activity-rename";
import { displayActivities } from "@/lib/activity-rename";
import { variantHref } from "@/lib/dashboards/drill";
import { formatNumber } from "@/lib/format";

const MAX_ACTIVITIES = 8;

function summarise(activities: string[]): string {
  if (activities.length <= MAX_ACTIVITIES) return activities.join(" → ");
  const head = activities.slice(0, 2).join(" → ");
  const tail = activities.slice(-2).join(" → ");
  return `${head} → … (${activities.length - 4}) … → ${tail}`;
}

export function TopVariantsList({
  logId,
  variants,
  totalContaining,
  renameMap,
}: {
  logId: string;
  variants: VariantRow[];
  totalContaining: number;
  renameMap: ActivityRenameMap;
}) {
  if (variants.length === 0) {
    return (
      <p className="px-4 py-8 text-center text-sm text-muted-foreground">
        No variants contain this activity.
      </p>
    );
  }
  return (
    <>
      <Table>
        <TableHeader className="bg-muted/30">
          <TableRow>
            <TableHead className="w-[52px] text-right">#</TableHead>
            <TableHead>Sequence</TableHead>
            <TableHead className="text-right">Cases</TableHead>
            <TableHead className="w-[90px] text-right">Share</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {variants.map((v) => {
            const display = displayActivities(v.activities, renameMap);
            return (
              <TableRow key={v.variant_id} className="h-12 cursor-pointer hover:bg-muted/40">
                <TableCell className="text-right tabular-nums text-xs text-muted-foreground">
                  <Link href={variantHref(logId, v.variant_id)} className="block">
                    {v.rank}
                  </Link>
                </TableCell>
                <TableCell>
                  <Link
                    href={variantHref(logId, v.variant_id)}
                    className="block truncate hover:underline underline-offset-2"
                    title={display.join(" → ")}
                  >
                    {summarise(display)}
                  </Link>
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatNumber(v.case_count)}
                </TableCell>
                <TableCell className="text-right tabular-nums text-muted-foreground">
                  {`${(v.case_pct * 100).toFixed(1)}%`}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      {totalContaining > variants.length && (
        <p className="border-t px-4 py-2 text-xs text-muted-foreground">
          Showing the top {variants.length} of {formatNumber(totalContaining)} variants containing
          this activity.
        </p>
      )}
    </>
  );
}
