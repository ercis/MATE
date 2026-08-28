"use client";

import { MetricInfoHint } from "../panel/metric-info";
import { formatMetric, useComplexityV2 } from "../panel/queries";
import {CardShell} from "@/components/dashboards/kit";

/** Every Table 3.3 metric, grouped by category, as a compact label→value list. */
export default function ComplexityCategories({ logId }: { logId: string }) {
  const { data, isLoading, isError } = useComplexityV2(logId);

  return (
    <CardShell loading={isLoading} empty={isError || !data}>
      {data && (
        <div className="h-full space-y-3 overflow-auto pr-1">
          {data.groups.map((group) => (
            <div key={group.category}>
              <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                {group.category}
              </div>
              <table className="w-full text-xs">
                <tbody>
                  {group.items.map((item) => (
                    <tr key={item.key} className="border-t border-border/50">
                      <td className="py-1 pr-2">
                        <code className="rounded bg-muted px-1 py-0.5 text-[10px]">
                          {item.label}
                        </code>
                      </td>
                      <td className="py-1 pr-2 text-muted-foreground">
                        <span className="inline-flex items-center gap-1">
                          {item.name}
                          <MetricInfoHint metricKey={item.key} />
                        </span>
                      </td>
                      <td className="py-1 text-right tabular-nums">
                        {formatMetric(item.key, item.value, data.values)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
        </div>
      )}
    </CardShell>
  );
}
