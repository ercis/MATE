"use client";

import { metricInfoContent } from "../panel/metric-info";
import { formatMetric, useComplexityV2 } from "../panel/queries";
import { CardShell, KpiGrid, KpiTile } from "@/components/dashboards/kit";

/**
 * The measures this card can show. Keys must match `kpis:` in `manifest.yaml`
 * and the keys in `panel/metric-info`, so the ⓘ text and citation come from
 * the same place as the panel's.
 */
const TILES: { key: string; label: string; hint?: string }[] = [
    { key: "n_events", label: "#-e", hint: "events" },
    { key: "n_event_types", label: "#-et", hint: "event types" },
    { key: "perc_unique_seq", label: "perc-unique-seq" },
    { key: "nseq_e", label: "nseq-e", hint: "normalized" },
    { key: "activity_var", label: "activity-var", hint: "Shannon" },
    { key: "avg_edit_distance", label: "avg-edit-distance" },
    { key: "structural_var", label: "structural-var" },
    { key: "n_acyclic_paths", label: "#-acyclic-paths" },
];

/** Headline metrics across the thesis categories, one KPI tile each. */
export default function ComplexitySuite({
  logId,
  config,
}: {
  logId: string;
  config?: Record<string, unknown>;
}) {
  const { data, isLoading, isError } = useComplexityV2(logId);
  const v = data?.values;

  // No stored selection shows everything — a card placed before this was
  // subsettable must never come back empty.
  const chosen = Array.isArray(config?.kpis) ? (config.kpis as string[]) : null;
  const shown = chosen ? TILES.filter((t) => chosen.includes(t.key)) : TILES;

  return (
    <CardShell loading={isLoading} error={isError} empty={!v || shown.length === 0}>
      {v && (
        <KpiGrid minTileWidth={140}>
          {shown.map((t) => (
            <KpiTile
              key={t.key}
              label={t.label}
              value={formatMetric(t.key, v[t.key] ?? null, v)}
              hint={t.hint}
              info={metricInfoContent(t.key)}
            />
          ))}
        </KpiGrid>
      )}
    </CardShell>
  );
}
