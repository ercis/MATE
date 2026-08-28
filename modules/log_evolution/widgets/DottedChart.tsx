"use client";

import { useMemo, useState } from "react";
import { Check, ListFilter } from "lucide-react";
import { Popover } from "radix-ui";
import {
  CartesianGrid,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";

import {ChartFrame, LegendDot} from "@/components/dashboards/kit";
import { cn } from "@/lib/cn";

import { colorAt, formatDay, useDotted } from "../panel/queries";

const fillFor = (name: string, i: number) =>
  name === "Other" ? "var(--muted-foreground)" : colorAt(i);

/**
 * The classic dotted chart: one dot per event, x = time, y = case (ordered by
 * start time), colour = activity. Reveals batching, arrival patterns and drift
 * at a glance. `config.max_points` caps the rendered events (down-sampled above).
 */
export default function DottedChart({
  logId,
  config,
}: {
  logId: string;
  config?: Record<string, unknown>;
}) {
  const maxPoints = typeof config?.max_points === "number" ? config.max_points : 8000;
  const { data, isLoading, isError } = useDotted(logId, maxPoints);

  // Hidden activities (empty set = all visible, the default). Colours key off
  // the activity's index in the *full* list, so filtering never repaints the
  // surviving series.
  const [hidden, setHidden] = useState<ReadonlySet<string>>(new Set());

  // One Scatter series per activity → native per-activity colour.
  const series = useMemo(() => {
    if (!data) return [] as { name: string; index: number; points: { t: number; y: number }[] }[];
    const buckets: { t: number; y: number }[][] = data.activities.map(() => []);
    for (const pt of data.points) {
      (buckets[pt.a] ??= []).push({ t: pt.t, y: pt.y });
    }
    return data.activities.map((name, index) => ({ name, index, points: buckets[index] ?? [] }));
  }, [data]);

  const visibleSeries = series.filter((s) => !hidden.has(s.name));

  const yMax = data && data.n_cases > 0 ? data.n_cases - 1 : "dataMax";

  return (
    <ChartFrame
      loading={isLoading}
      empty={isError || !data || data.points.length === 0}
      emptyText="No events to chart for this log yet."
      legend={
        series.length > 0 ? (
          <>
            <ActivityFilter
              options={series.map((s) => ({ name: s.name, color: fillFor(s.name, s.index) }))}
              hidden={hidden}
              onChange={setHidden}
            />
            {visibleSeries.map((s) => (
              <LegendDot key={s.name} color={fillFor(s.name, s.index)} label={s.name} />
            ))}
          </>
        ) : undefined
      }
      caption={
        data?.sampled
          ? `Showing ${data.points.length.toLocaleString()} of ${data.total_events.toLocaleString()} events (sampled).`
          : undefined
      }
    >
      <ResponsiveContainer width="100%" height="100%">
        <ScatterChart margin={{ left: 0, right: 8, top: 4, bottom: 4 }}>
          <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
          <XAxis
            type="number"
            dataKey="t"
            name="Time"
            domain={["dataMin", "dataMax"]}
            tickFormatter={(v: number) => formatDay(v)}
            tick={{ fontSize: 9, fill: "var(--muted-foreground)" }}
            stroke="var(--border)"
          />
          <YAxis
            type="number"
            dataKey="y"
            name="Case"
            reversed
            domain={[0, yMax]}
            width={36}
            tick={{ fontSize: 9, fill: "var(--muted-foreground)" }}
            stroke="var(--border)"
          />
          <ZAxis range={[6, 6]} />
          <Tooltip
            cursor={{ strokeDasharray: "3 3" }}
            contentStyle={{ fontSize: 12 }}
            labelFormatter={() => ""}
            formatter={(value, name) =>
              name === "Time"
                ? [formatDay(Number(value)), "Time"]
                : [String(value), String(name)]
            }
          />
          {visibleSeries.map((s) => (
            <Scatter
              key={s.name}
              name={s.name}
              data={s.points}
              fill={fillFor(s.name, s.index)}
              isAnimationActive={false}
            />
          ))}
        </ScatterChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

// Multi-select dropdown for the legend row: a popover with a checkable
// activity list, defaulting to everything visible. Built on the raw radix
// `Popover` primitive (the shadcn popover/checkbox wrappers aren't bundler
// runtime-externals), styled to match `components/ui/popover.tsx`.
function ActivityFilter({
  options,
  hidden,
  onChange,
}: {
  options: { name: string; color: string }[];
  hidden: ReadonlySet<string>;
  onChange: (next: ReadonlySet<string>) => void;
}) {
  const toggle = (name: string) => {
    const next = new Set(hidden);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    onChange(next);
  };
  const visible = options.length - options.filter((o) => hidden.has(o.name)).length;
  const filtered = visible < options.length;

  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <button
          type="button"
          className={cn(
            "inline-flex h-5 cursor-pointer items-center gap-1 rounded border bg-card px-1.5 text-[10px] font-medium transition-colors hover:bg-accent hover:text-accent-foreground",
            filtered ? "text-foreground" : "text-muted-foreground",
          )}
          title="Choose which activities are shown"
        >
          <ListFilter className="size-3" />
          {filtered ? (
            <span className="tabular-nums">
              {visible}/{options.length}
            </span>
          ) : (
            "Activities"
          )}
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align="start"
          sideOffset={4}
          className="z-50 w-56 origin-(--radix-popover-content-transform-origin) rounded-md border bg-popover p-1 text-popover-foreground shadow-md outline-hidden data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95"
        >
          {filtered && (
            <button
              type="button"
              onClick={() => onChange(new Set())}
              className="flex w-full cursor-pointer items-center gap-2 rounded-sm px-2 py-1.5 text-left text-xs font-medium text-primary outline-none hover:bg-accent hover:text-accent-foreground"
            >
              Show all {options.length}
            </button>
          )}
          {visible > 0 && (
            <button
              type="button"
              onClick={() => onChange(new Set(options.map((o) => o.name)))}
              className="flex w-full cursor-pointer items-center gap-2 rounded-sm px-2 py-1.5 text-left text-xs font-medium text-primary outline-none hover:bg-accent hover:text-accent-foreground"
            >
              Hide all {options.length}
            </button>
          )}
          <div className="max-h-56 overflow-y-auto">
            {options.map((o) => {
              const on = !hidden.has(o.name);
              return (
                <button
                  key={o.name}
                  type="button"
                  onClick={() => toggle(o.name)}
                  className={cn(
                    "flex w-full cursor-pointer items-center gap-2 rounded-sm px-2 py-1.5 text-left text-xs outline-none hover:bg-accent hover:text-accent-foreground",
                    on ? "font-medium" : "text-muted-foreground",
                  )}
                >
                  <Check className={cn("size-3.5 shrink-0", on ? "opacity-100" : "opacity-0")} />
                  <span
                    aria-hidden
                    className="inline-block size-2.5 shrink-0 rounded-[2px]"
                    style={{ backgroundColor: o.color }}
                  />
                  <span className="truncate" title={o.name}>
                    {o.name}
                  </span>
                </button>
              );
            })}
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
