"use client";

import { useMemo, useState } from "react";

import { formatNumber } from "@/lib/format";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import { useOcdfg } from "../panel/queries";
import { CardShell } from "./_kit";

/** Object-centric directly-follows graph (OC-DFG): per object type, the
 * activity→activity edges with the number of unique objects that traverse
 * them. Rendered as a compact ranked edge list rather than a heavy graph. */
export default function Ocdfg({ logId }: { logId: string }) {
  const { data, isLoading, isError } = useOcdfg(logId);
  const [objectType, setObjectType] = useState<string | null>(null);

  const activeType = objectType ?? data?.object_types[0] ?? null;

  const edges = useMemo(
    () => (data && activeType ? data.edges.filter((e) => e.object_type === activeType) : []),
    [data, activeType],
  );

  return (
    <CardShell loading={isLoading} empty={isError || !data || data.object_types.length === 0}>
      {data && activeType && (
        <div className="flex h-full flex-col gap-3">
          <Select value={activeType} onValueChange={setObjectType}>
            <SelectTrigger className="h-8 w-[200px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {data.object_types.map((t) => (
                <SelectItem key={t} value={t}>
                  {t}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <div className="min-h-0 flex-1 overflow-y-auto">
            {edges.length === 0 ? (
              <p className="py-6 text-center text-xs text-muted-foreground">
                No directly-follows edges for this object type.
              </p>
            ) : (
              <ul className="space-y-1">
                {edges.map((e) => (
                  <li
                    key={`${e.source}→${e.target}`}
                    className="flex items-center justify-between gap-2 rounded-md border border-border/60 bg-muted/20 px-2.5 py-1.5 text-xs"
                  >
                    <span className="truncate font-mono">
                      {e.source} <span className="text-muted-foreground">→</span> {e.target}
                    </span>
                    <span className="shrink-0 tabular-nums text-muted-foreground">
                      {formatNumber(e.count)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </CardShell>
  );
}
