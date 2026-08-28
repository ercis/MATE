"use client";

import { useState } from "react";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useOcelObjectTypes, useOcelOverview, useOcelRelationships } from "@/lib/queries";
import { OCEL_PAGE_SIZE } from "@/lib/query-keys";
import { OcelDataTable } from "./ocel-data-table";

// Shared with prefetchProcessTabs (client-prefetch.ts) for query-key parity.
const LIMIT = OCEL_PAGE_SIZE;
const ALL = "__all__";

/** The flattened event↔object relations table – which objects each event
 * touched, with the (optional) qualifier. */
export function OcelRelationshipsTab({ logId }: { logId: string }) {
  const [offset, setOffset] = useState(0);
  const [objectType, setObjectType] = useState<string>(ALL);
  const [activity, setActivity] = useState<string>(ALL);

  const { data: types } = useOcelObjectTypes(logId);
  const { data: overview } = useOcelOverview(logId);
  const { data, isLoading, isError, error } = useOcelRelationships(logId, {
    offset,
    limit: LIMIT,
    object_type: objectType === ALL ? undefined : objectType,
    activity: activity === ALL ? undefined : activity,
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Select
          value={objectType}
          onValueChange={(v) => {
            setObjectType(v);
            setOffset(0);
          }}
        >
          <SelectTrigger className="h-9 w-[200px]">
            <SelectValue placeholder="All object types" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All object types</SelectItem>
            {(types ?? []).map((t) => (
              <SelectItem key={t.type} value={t.type}>
                {t.type}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select
          value={activity}
          onValueChange={(v) => {
            setActivity(v);
            setOffset(0);
          }}
        >
          <SelectTrigger className="h-9 w-[240px]">
            <SelectValue placeholder="All activities" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All activities</SelectItem>
            {(overview?.activities ?? []).map((a) => (
              <SelectItem key={a} value={a}>
                {a}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <OcelDataTable
        page={data}
        isLoading={isLoading}
        isError={isError}
        error={error}
        offset={offset}
        limit={LIMIT}
        onOffsetChange={setOffset}
        emptyLabel="No relationships match the filter."
      />
    </div>
  );
}
