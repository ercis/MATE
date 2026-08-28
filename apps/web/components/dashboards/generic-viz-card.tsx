"use client";

import { Skeleton } from "@/components/ui/skeleton";
import { VizEmpty } from "@/components/visualizations/viz-shell";
import { FieldMappingForm, type VizPatch } from "@/components/visualizations/field-mapping-form";
import { configWithoutWidgetFilter } from "@/components/dashboards/widget-filter";
import { vizRegistry } from "@/lib/visualizations/registry";
import { useDatasetData } from "@/lib/visualizations/use-dataset-data";
import type { DashboardItem } from "@/lib/dashboard-queries";
import type { FieldMapping } from "@/lib/visualizations/types";

/**
 * Body of a `kind:"viz"` dashboard card. Fetches + normalizes the module
 * dataset (`useDatasetData`) and renders the chosen generic visualization
 * against it, honoring the board's global filters automatically. Shows an
 * unconfigured hint until a visualization is chosen.
 */
export function GenericVizBody({ item, logId }: { item: DashboardItem; logId: string | null }) {
  const { envelope, isLoading, isError, missing } = useDatasetData(item, logId);
  const spec = item.viz_id ? vizRegistry[item.viz_id] : undefined;

  if (!logId) return <VizEmpty message="Select an event log to populate this card." />;
  if (missing) return <VizEmpty message="This dataset's module isn't installed." />;
  if (!item.viz_id || !spec) return <VizEmpty message="Open settings to choose a visualization." />;
  if (isLoading) return <Skeleton className="h-full w-full" />;
  if (isError || !envelope) return <VizEmpty message="Couldn't load this dataset." />;
  if (!spec.accepts.includes(envelope.shape)) {
    return <VizEmpty message="This visualization can't render this dataset." />;
  }

  const Viz = spec.Component;
  return (
    <Viz
      dataset={envelope}
      mapping={(item.mapping ?? {}) as FieldMapping}
      // Strip the reserved per-widget-filter key so the viz never sees it.
      options={configWithoutWidgetFilter(item.config) as Record<string, unknown>}
    />
  );
}

/** Settings popover content for a viz card - wires the field-mapping form to the
 * (cached) dataset so it can offer column bindings. */
export function VizSettings({
  item,
  logId,
  onChange,
}: {
  item: DashboardItem;
  logId: string | null;
  onChange: (patch: VizPatch) => void;
}) {
  const { envelope, shape } = useDatasetData(item, logId);
  // The per-card filter editor used to sit below the mapping form. It's gone:
  // one filter row above the board scopes everything, and a second filter
  // hidden inside a single card contradicts it. A filter already stored on a
  // card is still applied (`readWidgetFilter` in `use-dataset-data`) so no
  // existing board silently changes what it shows — there is just no longer a
  // way to author a new one.
  return <FieldMappingForm item={item} dataset={envelope} shape={shape} onChange={onChange} />;
}
