"use client";

import { CardShell, KpiGrid, KpiTile } from "@/components/dashboards/kit";
import { formatNumber } from "@/lib/format";

import { metricInfoContent } from "../panel/metric-info";
import { useComplexityMetrics } from "../panel/queries";

const fmt = (n: number | null | undefined, digits = 2) =>
  n == null ? "–" : n.toLocaleString(undefined, { maximumFractionDigits: digits });

type Basic = {
  magnitude: number;
  variety: number;
  distinct_traces_pct: number;
  trace_length_avg: number;
  normalized_variant_entropy: number;
  normalized_sequence_entropy: number;
  lempel_ziv: number;
  pentland_process: number;
};

/**
 * The measures this card can show. Ids match both `kpis:` in `manifest.yaml`
 * and the keys in `panel/metric-info`, so a card's ⓘ text and citation come
 * from the same place as the panel's.
 */
const FIGURES: {
  id: string;
  label: string;
  value: (m: Basic) => string;
  hint?: string;
}[] = [
  { id: "magnitude", label: "Magnitude", value: (m) => formatNumber(m.magnitude), hint: "events" },
  {
    id: "variety",
    label: "Variety",
    value: (m) => formatNumber(m.variety),
    hint: "distinct activities",
  },
  {
    id: "distinct_traces_pct",
    label: "Distinct traces",
    value: (m) => `${fmt(m.distinct_traces_pct * 100, 1)}%`,
  },
  { id: "trace_length", label: "Avg trace len", value: (m) => fmt(m.trace_length_avg, 1) },
  {
    id: "variant_entropy",
    label: "Variant entropy",
    value: (m) => fmt(m.normalized_variant_entropy),
  },
  {
    id: "sequence_entropy",
    label: "Sequence entropy",
    value: (m) => fmt(m.normalized_sequence_entropy),
  },
  { id: "lempel_ziv", label: "Lempel–Ziv", value: (m) => fmt(m.lempel_ziv) },
  {
    id: "pentland_process",
    label: "Pentland",
    value: (m) => fmt(m.pentland_process),
    hint: "process",
  },
];

/** Headline EPA-based complexity measures for the log. */
export default function ComplexityMetrics({
  logId,
  config,
}: {
  logId: string;
  config?: Record<string, unknown>;
}) {
  const { data, isLoading, isError } = useComplexityMetrics(logId);
  const m = data?.basic as Basic | undefined;

  // No stored selection shows everything — a card placed before this was
  // subsettable must never come back empty.
  const chosen = Array.isArray(config?.kpis) ? (config.kpis as string[]) : null;
  const shown = chosen ? FIGURES.filter((f) => chosen.includes(f.id)) : FIGURES;

  return (
    <CardShell loading={isLoading} error={isError} empty={!m || shown.length === 0}>
      {m && (
        <KpiGrid minTileWidth={140}>
          {shown.map((f) => (
            <KpiTile
              key={f.id}
              label={f.label}
              value={f.value(m)}
              hint={f.hint}
              info={metricInfoContent(f.id)}
            />
          ))}
        </KpiGrid>
      )}
    </CardShell>
  );
}
