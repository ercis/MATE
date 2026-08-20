"use client";

import { formatMetric, useComplexityV2 } from "../panel/queries";
import { CardShell, KpiTile } from "./_kit";

/** Headline metrics across the thesis categories, one KPI tile each. */
export default function ComplexitySuite({ logId }: { logId: string }) {
  const { data, isLoading, isError } = useComplexityV2(logId);
  const v = data?.values;

  const tiles: { key: string; label: string; hint?: string }[] = [
    { key: "n_events", label: "#-e", hint: "events" },
    { key: "n_event_types", label: "#-et", hint: "event types" },
    { key: "perc_unique_seq", label: "perc-unique-seq" },
    { key: "nseq_e", label: "nseq-e", hint: "normalized" },
    { key: "activity_var", label: "activity-var", hint: "Shannon" },
    { key: "avg_edit_distance", label: "avg-edit-distance" },
    { key: "structural_var", label: "structural-var" },
    { key: "n_acyclic_paths", label: "#-acyclic-paths" },
  ];

  return (
    <CardShell loading={isLoading} empty={isError || !v}>
      {v && (
        <div className="grid grid-cols-2 gap-2">
          {tiles.map((t) => (
            <KpiTile
              key={t.key}
              label={t.label}
              value={formatMetric(t.key, v[t.key] ?? null, v)}
              hint={t.hint}
            />
          ))}
        </div>
      )}
    </CardShell>
  );
}
