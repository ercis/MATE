"use client";

import { CardShell, KpiGrid, KpiTile } from "@/components/dashboards/kit";
import { formatDuration, formatNumber } from "@/lib/format";
import type { DrillHandler } from "@/lib/dashboards/drill";

import { usePerformanceKpis } from "../panel/queries";

type Summary = {
  cases: number;
  events: number;
  variants: number;
  throughput_cases_per_day: number;
  avg_cycle_time_s: number;
  median_cycle_time_s: number;
  p90_cycle_time_s: number;
  lead_time_s: number;
};

/**
 * The figures this card can show, in display order.
 *
 * The ids must match the `kpis:` list in `manifest.yaml` — that is what the
 * card's settings panel offers, and the chosen subset arrives as
 * `config.kpis`. Showing three tiles instead of eight is what lets the same
 * card sit in a narrow slot without becoming unreadable.
 */
const FIGURES: {
  id: string;
  label: string;
  value: (s: Summary) => string;
  info: string;
}[] = [
  {
    id: "cases",
    label: "Cases",
    value: (s) => formatNumber(s.cases),
    info: "Number of cases (process runs, e.g. orders or tickets) in this log.",
  },
  {
    id: "events",
    label: "Events",
    value: (s) => formatNumber(s.events),
    info: "Total number of recorded steps across all cases.",
  },
  {
    id: "variants",
    label: "Variants",
    value: (s) => formatNumber(s.variants),
    info: "Number of distinct paths cases take through the process. Many variants = little standardisation.",
  },
  {
    id: "throughput",
    label: "Throughput",
    value: (s) => `${formatNumber(s.throughput_cases_per_day)}/day`,
    info: "How many cases the process handles per day on average, over the log's time span.",
  },
  {
    id: "avg_cycle",
    label: "Avg cycle",
    value: (s) => formatDuration(s.avg_cycle_time_s),
    info: "Average time a case takes from its first to its last recorded event.",
  },
  {
    id: "median_cycle",
    label: "Median cycle",
    value: (s) => formatDuration(s.median_cycle_time_s),
    info: "Half of all cases finish within this time — barely affected by rare, very slow cases.",
  },
  {
    id: "p90_cycle",
    label: "P90 cycle",
    value: (s) => formatDuration(s.p90_cycle_time_s),
    info: "90% of cases finish within this time; only the slowest 10% take longer.",
  },
  {
    id: "lead_time",
    label: "Lead time",
    value: (s) => formatDuration(s.lead_time_s),
    info: "End-to-end time a case spends in the process (first to last event), including waiting between steps.",
  },
];

/** Headline timing figures. A placement can show any subset (see FIGURES). */
export default function KpiOverview({
  logId,
  config,
  onDrill,
}: {
  logId: string;
  config?: Record<string, unknown>;
  onDrill?: DrillHandler;
}) {
  const { data, isLoading, isError } = usePerformanceKpis(logId);
  const s = data?.summary as Summary | undefined;

  // No selection stored (a card placed before this was subsettable, or one the
  // user hasn't narrowed) shows everything — never an empty card.
  const chosen = Array.isArray(config?.kpis) ? (config.kpis as string[]) : null;
  const shown = chosen ? FIGURES.filter((f) => chosen.includes(f.id)) : FIGURES;

  return (
    <CardShell loading={isLoading} error={isError} empty={!s || shown.length === 0}>
      {s && (
        <KpiGrid>
          {shown.map((f) => (
            <KpiTile
              key={f.id}
              label={f.label}
              value={f.value(s)}
              info={f.info}
              onClick={onDrill ? () => onDrill({ params: { metric: f.id } }) : undefined}
            />
          ))}
        </KpiGrid>
      )}
    </CardShell>
  );
}
