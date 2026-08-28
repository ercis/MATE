"use client";

import { useObjectsSummary } from "../panel/queries";
import { ObjectTypeStats } from "../panel/ObjectsSummary";
import {CardShell} from "@/components/dashboards/kit";

/** Per-object-type lifecycle KPIs card (median lifecycle, avg events). */
export default function ObjectLifecycle({ logId }: { logId: string }) {
  const { data, isLoading, isError } = useObjectsSummary(logId);
  return (
    <CardShell loading={isLoading} empty={isError || !data || data.types.length === 0}>
      {data && (
        <div className="h-full overflow-auto">
          <ObjectTypeStats types={data.types} hasInteracting={data.has_interacting} compact />
        </div>
      )}
    </CardShell>
  );
}
