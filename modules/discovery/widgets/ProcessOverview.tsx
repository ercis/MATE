"use client";

import { CardShell, KpiGrid, KpiTile } from "@/components/dashboards/kit";
import { formatNumber } from "@/lib/format";
import type { DrillHandler } from "@/lib/dashboards/drill";

import { useDiscoveryDfg } from "../panel/queries";

type Dfg = {
  activities: unknown[];
  edges: unknown[];
  start_activities: Record<string, unknown>;
  end_activities: Record<string, unknown>;
};

/**
 * The figures this card can show. Ids must match `kpis:` in `manifest.yaml`;
 * the chosen subset arrives as `config.kpis`.
 */
const FIGURES: {
  id: string;
  label: string;
  value: (d: Dfg) => string;
  info: string;
}[] = [
  {
    id: "activities",
    label: "Activities",
    value: (d) => formatNumber(d.activities.length),
    info: "How many distinct steps appear in the discovered process.",
  },
  {
    id: "connections",
    label: "Connections",
    value: (d) => formatNumber(d.edges.length),
    info: "How many distinct step-to-step transitions occur. Many connections relative to activities means cases take widely varying routes.",
  },
  {
    id: "start_activities",
    label: "Start activities",
    value: (d) => formatNumber(Object.keys(d.start_activities).length),
    info: "How many different steps a case can begin with. More than one or two often means the log mixes several processes, or that cases are cut off at the extraction boundary.",
  },
  {
    id: "end_activities",
    label: "End activities",
    value: (d) => formatNumber(Object.keys(d.end_activities).length),
    info: "How many different steps a case can finish on. Many endings usually means cases drop out at various points rather than completing.",
  },
];

/** Structural summary of the discovered directly-follows graph. */
export default function ProcessOverview({
  logId,
  config,
  onDrill,
}: {
  logId: string;
  config?: Record<string, unknown>;
  onDrill?: DrillHandler;
}) {
  const { data, isLoading, isError } = useDiscoveryDfg(logId);
  const d = data as Dfg | undefined;

  // No stored selection shows everything — a card placed before this was
  // subsettable must never come back empty.
  const chosen = Array.isArray(config?.kpis) ? (config.kpis as string[]) : null;
  const shown = chosen ? FIGURES.filter((f) => chosen.includes(f.id)) : FIGURES;

  return (
    <CardShell loading={isLoading} error={isError} empty={!d || shown.length === 0}>
      {d && (
        <KpiGrid minTileWidth={130}>
          {shown.map((f) => (
            <KpiTile
              key={f.id}
              label={f.label}
              value={f.value(d)}
              info={f.info}
              onClick={onDrill ? () => onDrill() : undefined}
            />
          ))}
        </KpiGrid>
      )}
    </CardShell>
  );
}
