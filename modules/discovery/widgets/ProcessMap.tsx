"use client";

import { DfgCanvas } from "../panel/canvases/DfgCanvas";
import { DiscoverySettingsProvider } from "../panel/discovery-settings-context";
import { useDiscoveryDfg } from "../panel/queries";
import {CardShell} from "@/components/dashboards/kit";

/**
 * Directly-follows graph of the dashboard-filtered event log.
 *
 * `useDiscoveryDfg` goes through the shared `@/lib/api` fetch wrapper, so the
 * dashboard's ephemeral `X-FF-Event-Filter` header (column filters + time
 * range) is attached automatically – the map re-renders against the filtered
 * slice with no extra plumbing. Wrapping the real `DfgCanvas` in the discovery
 * settings provider means the widget honours the same render settings (layout,
 * edge labels, sliders) the user configured in the discovery panel.
 */
export default function ProcessMap({ logId }: { logId: string }) {
  const { data, isLoading, isError } = useDiscoveryDfg(logId);

  return (
    <CardShell
      loading={isLoading}
      empty={isError || !data || data.activities.length === 0}
      emptyText="No process map for the current filter."
    >
      {data && (
        <DiscoverySettingsProvider logId={logId} moduleId="discovery">
          <DfgCanvas
            data={data}
            shellClassName="h-full min-h-[240px] w-full overflow-hidden rounded-lg border bg-card"
          />
        </DiscoverySettingsProvider>
      )}
    </CardShell>
  );
}
