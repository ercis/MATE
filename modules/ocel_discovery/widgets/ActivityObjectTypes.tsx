"use client";

import { useActivityObjectTypes } from "../panel/queries";
import { MatrixHeatmap } from "../panel/MatrixHeatmap";
import {CardShell} from "@/components/dashboards/kit";

/** Compact activity / object-type heatmap card: how many events of each
 *  activity touch each object type. */
export default function ActivityObjectTypes({ logId }: { logId: string }) {
  const { data, isLoading, isError } = useActivityObjectTypes(logId);
  return (
    <CardShell loading={isLoading} empty={isError || !data || data.cells.length === 0}>
      {data && (
        <div className="h-full overflow-auto">
          <MatrixHeatmap data={data} metric="events" compact />
        </div>
      )}
    </CardShell>
  );
}
