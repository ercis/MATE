"use client";

import {
  Sparkles,
  TrendingDown,
  TrendingUp,
  Waves,
  Zap,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import type { CdeDrift } from "./queries";

const DRIFT_META: Record<
  string,
  { icon: LucideIcon; colour: string; label: string }
> = {
  sudden: { icon: Zap, colour: "rgb(120,120,120)", label: "Sudden" },
  gradual: { icon: TrendingUp, colour: "rgb(30,144,255)", label: "Gradual" },
  incremental: {
    icon: TrendingDown,
    colour: "rgb(217,70,239)",
    label: "Incremental",
  },
  recurring: { icon: Waves, colour: "rgb(34,211,238)", label: "Recurring" },
};

function fmtTs(iso: string): string {
  if (!iso) return "–";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function DriftTable({
  drifts,
  selectedKey,
  onSelect,
}: {
  drifts: CdeDrift[];
  selectedKey: string | null;
  onSelect: (key: string) => void;
}) {
  if (drifts.length === 0) {
    return (
      <p className="py-6 text-center text-xs text-muted-foreground">
        No drifts available yet – run CV4CDD against this log first.
      </p>
    );
  }

  return (
    <div className="rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="text-xs">Type</TableHead>
            <TableHead className="text-xs">Start</TableHead>
            <TableHead className="text-xs">Activities</TableHead>
            <TableHead className="text-right text-xs">Confidence</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {drifts.map((d) => {
            const meta = DRIFT_META[d.type] ?? {
              icon: Sparkles,
              colour: "var(--foreground)",
              label: d.type,
            };
            const Icon = meta.icon;
            const isSelected = d.drift_key === selectedKey;
            return (
              <TableRow
                key={d.drift_key}
                onClick={() => onSelect(d.drift_key)}
                data-state={isSelected ? "selected" : undefined}
                className="cursor-pointer"
              >
                <TableCell className="text-xs">
                  <span
                    className="inline-flex items-center gap-1.5 font-medium"
                    style={{ color: meta.colour }}
                  >
                    <Icon className="h-3 w-3" />
                    {meta.label}
                  </span>
                </TableCell>
                <TableCell className="text-xs tabular-nums text-muted-foreground">
                  {fmtTs(d.start_timestamp)}
                </TableCell>
                <TableCell className="text-xs">
                  <div className="flex flex-col gap-0.5">
                    <span className="truncate">
                      {d.start_activity || <em>–</em>}
                    </span>
                    <span className="text-muted-foreground">↓</span>
                    <span className="truncate">
                      {d.end_activity || <em>–</em>}
                    </span>
                  </div>
                </TableCell>
                <TableCell className="text-right text-xs tabular-nums">
                  {(d.confidence * 100).toFixed(1)}%
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}

export function DriftTypeLegend({ drifts }: { drifts: CdeDrift[] }) {
  const counts: Record<string, number> = {};
  for (const d of drifts) counts[d.type] = (counts[d.type] ?? 0) + 1;
  if (Object.keys(counts).length === 0) return null;

  return (
    <div className="flex flex-wrap gap-1.5">
      {Object.entries(counts).map(([type, n]) => {
        const meta = DRIFT_META[type];
        const Icon = meta?.icon ?? Sparkles;
        return (
          <Badge
            key={type}
            variant="secondary"
            className="gap-1 border-0 px-2 py-0.5 text-[10px]"
            style={{
              background: `${meta?.colour ?? "var(--muted)"}22`,
              color: meta?.colour ?? "var(--foreground)",
            }}
          >
            <Icon className="h-3 w-3" />
            {meta?.label ?? type} · {n}
          </Badge>
        );
      })}
    </div>
  );
}
