"use client";

import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

import { MetricInfoHint } from "./metric-info";
import {
  formatMetric,
  useComplexityV2,
  useTransitionMatrix,
  type MetricGroup,
  type TransitionMatrix,
} from "./queries";

export default function ComplexityV2Panel({
  logId,
}: {
  logId: string;
  moduleId: string;
}) {
  const q = useComplexityV2(logId);

  if (q.isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-56" />
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-96 w-full" />
      </div>
    );
  }

  if (q.isError || !q.data) {
    return (
      <Card>
        <CardContent className="text-sm text-muted-foreground">
          Could not load complexity metrics. Re-import the log or check the
          module&rsquo;s logs.
        </CardContent>
      </Card>
    );
  }

  const data = q.data;

  return (
    <div className="space-y-6">
      <div className="grid gap-4 xl:grid-cols-2">
        {data.groups.map((group) => (
          <MetricTable
            key={group.category}
            group={group}
            values={data.values}
            enrichedSupported={data.enriched_supported}
          />
        ))}
      </div>
      <TransitionHeatmap logId={logId} />
    </div>
  );
}

// ── Per-category metric table ─────────────────────────────────────────────────

function MetricTable({
  group,
  values,
  enrichedSupported,
}: {
  group: MetricGroup;
  values: Record<string, number | null>;
  enrichedSupported: boolean;
}) {
  const enrichedUnavailable = group.category === "Enriched Entropy" && !enrichedSupported;
  const uniformSource =
    group.items.length > 0 && group.items.every((i) => i.source === group.items[0].source)
      ? group.items[0].source
      : null;

  return (
    <Card>
      <CardContent>
        <div className="mb-3 flex items-start justify-between gap-2">
          <div className="min-w-0">
            <h3 className="text-sm font-semibold">{group.category}</h3>
            {uniformSource ? (
              <div className="text-[11px] text-muted-foreground">{uniformSource}</div>
            ) : null}
          </div>
          {enrichedUnavailable ? (
            <span className="shrink-0 text-[11px] text-muted-foreground">
              XES standard attributes missing
            </span>
          ) : null}
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-muted-foreground">
              <tr>
                <th className="py-1.5 pr-3 font-medium">Metric</th>
                <th className="py-1.5 pr-3 font-medium tabular-nums">Value</th>
                <th className="hidden py-1.5 font-medium sm:table-cell">Description</th>
              </tr>
            </thead>
            <tbody>
              {group.items.map((item) => (
                <tr key={item.key} className="border-t border-border/60 align-top">
                  <td className="py-1.5 pr-3">
                    <div className="flex items-center gap-2">
                      <code className="rounded bg-muted px-1.5 py-0.5 text-[11px] tabular-nums">
                        {item.label}
                      </code>
                      <span>{item.name}</span>
                      <MetricInfoHint metricKey={item.key} />
                    </div>
                    {uniformSource ? null : (
                      <div className="text-[11px] text-muted-foreground">{item.source}</div>
                    )}
                  </td>
                  <td className="py-1.5 pr-3 tabular-nums">
                    {formatMetric(item.key, item.value, values)}
                  </td>
                  <td className="hidden py-1.5 text-xs text-muted-foreground sm:table-cell">
                    {item.description}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

// ── prob-act-pairs transition-probability heatmap ─────────────────────────────

function TransitionHeatmap({ logId }: { logId: string }) {
  const q = useTransitionMatrix(logId);
  const data = q.data;

  return (
    <Card>
      <CardContent>
        <div className="mb-1 flex items-center gap-2">
          <h3 className="text-sm font-semibold">Probability of action pairs</h3>
          <code className="rounded bg-muted px-1.5 py-0.5 text-[11px]">prob-act-pairs</code>
          <MetricInfoHint metricKey="prob_act_pairs" />
        </div>
        <p className="mb-3 text-xs text-muted-foreground">
          Row-stochastic directly-follows transition matrix (Grisold et al., 2022).
          Cell (row → column) is P(next activity | current activity).
          {data?.truncated ? " Showing the 25 most frequent activities." : ""}
        </p>
        {q.isLoading ? (
          <Skeleton className="h-72 w-full" />
        ) : !data || data.activities.length === 0 ? (
          <div className="text-sm text-muted-foreground">No transitions to display.</div>
        ) : (
          <HeatmapGrid matrix={data} />
        )}
      </CardContent>
    </Card>
  );
}

function HeatmapGrid({ matrix }: { matrix: TransitionMatrix }) {
  const { activities, matrix: m } = matrix;
  const n = activities.length;

  return (
    <div className="overflow-auto">
      <div
        className="grid gap-px text-[10px]"
        style={{ gridTemplateColumns: `minmax(80px, 140px) repeat(${n}, 16px)` }}
      >
        {/* Header row: empty corner + activity names rotated to fit the 16px columns */}
        <div />
        {activities.map((act, j) => (
          <div
            key={`h${j}`}
            className="flex items-end justify-center overflow-hidden text-muted-foreground"
            style={{ height: 140 }}
            title={act}
          >
            <span
              className="whitespace-nowrap"
              style={{
                writingMode: "vertical-rl",
                transform: "rotate(180deg)",
                maxHeight: 136,
                overflow: "hidden",
                textOverflow: "ellipsis",
                lineHeight: "16px",
              }}
            >
              {act}
            </span>
          </div>
        ))}
        {/* Body */}
        {activities.map((act, i) => (
          <Row key={`r${i}`} index={i} label={act} row={m[i]} />
        ))}
      </div>
    </div>
  );
}

function Row({ index, label, row }: { index: number; label: string; row: number[] }) {
  return (
    <>
      <div className="truncate pr-2 text-right text-muted-foreground tabular-nums" title={label}>
        <span className="mr-1 font-medium">{index + 1}.</span>
        {label}
      </div>
      {row.map((p, j) => (
        <div
          key={j}
          className="h-4 w-4"
          style={{ backgroundColor: cellColor(p) }}
          title={`${(p * 100).toFixed(1)}%`}
        />
      ))}
    </>
  );
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function cellColor(p: number): string {
  if (!Number.isFinite(p) || p <= 0) return "rgba(99,102,241,0.06)";
  // Indigo, opacity scaled by probability (eased so small values stay visible).
  const alpha = 0.12 + 0.88 * Math.sqrt(Math.min(1, p));
  return `rgba(79,70,229,${alpha.toFixed(3)})`;
}
