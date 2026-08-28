"use client";

import { Fragment, useState } from "react";
import Link from "next/link";
import { ArrowUpRight, ChevronDown, ChevronRight } from "lucide-react";

import { cn } from "@/lib/cn";
import { activityHref, variantHref } from "@/lib/dashboards/drill";
import { formatNumber } from "@/lib/format";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";

import { CONF_COLORS } from "./conformance-decorate";
import type { PerActivityDeviation, PerVariant, Technique } from "./types";

const DEVIATIONS_TITLE =
  "How often the recorded process differed from the reference model at this activity";
const LOG_MOVES_TITLE = "Happened in the log but not allowed by the model at that point";
const MODEL_MOVES_TITLE = "Required by the model but skipped in the log";

export function DeviationTable({
  logId,
  perActivity,
  perVariant,
  technique,
}: {
  logId: string;
  perActivity: PerActivityDeviation[];
  perVariant: PerVariant[];
  technique: Technique;
}) {
  const alignments = technique === "alignments";
  return (
    <Tabs defaultValue="activity" className="space-y-3">
      <TabsList>
        <TabsTrigger value="activity">By activity</TabsTrigger>
        <TabsTrigger value="variant">By variant</TabsTrigger>
      </TabsList>
      <TabsContent value="activity">
        <ActivityTable logId={logId} rows={perActivity} alignments={alignments} />
      </TabsContent>
      <TabsContent value="variant">
        <VariantTable logId={logId} rows={perVariant} />
      </TabsContent>
    </Tabs>
  );
}

function ActivityTable({
  logId,
  rows,
  alignments,
}: {
  logId: string;
  rows: PerActivityDeviation[];
  alignments: boolean;
}) {
  const max = rows.reduce((m, r) => Math.max(m, r.deviations), 0);
  if (rows.length === 0) {
    return <Empty>No activities to show.</Empty>;
  }
  return (
    <div className="overflow-x-auto rounded-md border border-border">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border bg-muted/40 text-left text-xs text-muted-foreground">
            <th className="px-3 py-2 font-medium">Activity</th>
            <th
              className="px-3 py-2 text-right font-medium"
              style={{ cursor: "help" }}
              title={DEVIATIONS_TITLE}
            >
              Deviations
            </th>
            {alignments ? (
              <>
                <th
                  className="px-3 py-2 text-right font-medium"
                  style={{ cursor: "help" }}
                  title={LOG_MOVES_TITLE}
                >
                  In log, not in model
                </th>
                <th
                  className="px-3 py-2 text-right font-medium"
                  style={{ cursor: "help" }}
                  title={MODEL_MOVES_TITLE}
                >
                  In model, skipped in log
                </th>
              </>
            ) : null}
            <th className="px-3 py-2 text-right font-medium">Cases</th>
            <th className="w-40 px-3 py-2 font-medium">Severity</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.activity} className="border-b border-border last:border-0 hover:bg-muted/30">
              <td className="px-3 py-2">
                {/* "not in log" rows name a model-only activity — no page to
                    link to, so those stay plain text. */}
                {r.matched ? (
                  <Link
                    href={activityHref(logId, r.activity)}
                    className="font-mono text-xs hover:underline underline-offset-2"
                  >
                    {r.activity}
                  </Link>
                ) : (
                  <span className="font-mono text-xs">{r.activity}</span>
                )}
                {!r.matched ? (
                  <span
                    className="ml-2 rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-500"
                    style={{ cursor: "help" }}
                    title="In the model but never recorded in the log - likely an activity-name mismatch"
                  >
                    not in log
                  </span>
                ) : null}
              </td>
              <td className="px-3 py-2 text-right tabular-nums">{formatNumber(r.deviations)}</td>
              {alignments ? (
                <>
                  <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">
                    {formatNumber(r.log_moves)}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">
                    {formatNumber(r.model_moves)}
                  </td>
                </>
              ) : null}
              <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">
                {formatNumber(r.cases_affected)}
              </td>
              <td className="px-3 py-2">
                <SeverityBar value={r.deviations} max={max} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function VariantTable({ logId, rows }: { logId: string; rows: PerVariant[] }) {
  const [open, setOpen] = useState<Set<string>>(new Set());
  const toggle = (id: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  if (rows.length === 0) {
    return <Empty>No variants to show.</Empty>;
  }
  return (
    <div className="overflow-x-auto rounded-md border border-border">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border bg-muted/40 text-left text-xs text-muted-foreground">
            <th className="px-3 py-2 font-medium">Variant</th>
            <th className="px-3 py-2 text-right font-medium">Cases</th>
            <th className="px-3 py-2 text-right font-medium">Avg fitness</th>
            <th className="px-3 py-2 text-right font-medium">Deviations</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => {
            const isOpen = open.has(r.variant_id);
            return (
              <Fragment key={r.variant_id}>
                <tr
                  className="group cursor-pointer border-b border-border last:border-0 hover:bg-muted/30"
                  onClick={() => toggle(r.variant_id)}
                >
                  <td className="px-3 py-2">
                    <span className="inline-flex items-center gap-1.5">
                      {isOpen ? (
                        <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
                      ) : (
                        <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
                      )}
                      <span className="font-medium">Variant {i + 1}</span>
                      <span className="text-xs text-muted-foreground">
                        · {r.activities.length} steps
                      </span>
                      {/* Row click keeps expand/collapse; this jumps to the
                          platform's canonical variant view. */}
                      <Link
                        href={variantHref(logId, r.variant_id)}
                        title="Open variant view"
                        aria-label={`Open variant view for variant ${i + 1}`}
                        className="ml-0.5 inline-flex align-middle text-muted-foreground opacity-0 transition-opacity hover:text-foreground group-hover:opacity-100"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <ArrowUpRight className="h-3.5 w-3.5" />
                      </Link>
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">{formatNumber(r.n_cases)}</td>
                  <td
                    className={cn(
                      "px-3 py-2 text-right tabular-nums",
                      r.avg_fitness >= 1 ? "text-emerald-600 dark:text-emerald-500" : "text-foreground",
                    )}
                  >
                    {r.avg_fitness.toFixed(3)}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">{formatNumber(r.deviations)}</td>
                </tr>
                {isOpen ? (
                  <tr className="border-b border-border bg-muted/20 last:border-0">
                    <td colSpan={4} className="px-3 py-2">
                      <div className="flex flex-wrap items-center gap-1">
                        {r.activities.map((a, idx) => (
                          <Fragment key={`${r.variant_id}-${idx}`}>
                            {idx > 0 ? (
                              <span className="text-muted-foreground/60">→</span>
                            ) : null}
                            <Link
                              href={activityHref(logId, a)}
                              className="rounded bg-background/70 px-1.5 py-0.5 font-mono text-[11px] hover:underline underline-offset-2"
                            >
                              {a}
                            </Link>
                          </Fragment>
                        ))}
                      </div>
                    </td>
                  </tr>
                ) : null}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function SeverityBar({ value, max }: { value: number; max: number }) {
  const pct = max > 0 ? Math.round((value / max) * 100) : 0;
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
      <div
        className="h-full rounded-full"
        // Inline colour: `bg-red-500/70` is not compiled for module sources.
        style={{ width: `${pct}%`, background: CONF_COLORS.chart, opacity: 0.75 }}
        aria-hidden
      />
    </div>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-md border border-border px-3 py-8 text-center text-xs text-muted-foreground">
      {children}
    </div>
  );
}
