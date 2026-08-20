"use client";

import { useState } from "react";
import { Search } from "lucide-react";

import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useOcelEvents, useOcelOverview } from "@/lib/queries";
import { OcelDataTable } from "./ocel-data-table";

const LIMIT = 100;
const ALL = "__all__";

export function OcelEventsTab({ logId }: { logId: string }) {
  const [offset, setOffset] = useState(0);
  const [activity, setActivity] = useState<string>(ALL);
  const [q, setQ] = useState("");

  const { data: overview } = useOcelOverview(logId);
  const { data, isLoading, isError, error } = useOcelEvents(logId, {
    offset,
    limit: LIMIT,
    activity: activity === ALL ? undefined : activity,
    q: q.trim() || undefined,
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
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
        <div className="relative min-w-[220px] max-w-md flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Filter by event id…"
            className="h-9 pl-8"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setOffset(0);
            }}
          />
        </div>
      </div>

      <OcelDataTable
        page={data}
        isLoading={isLoading}
        isError={isError}
        error={error}
        offset={offset}
        limit={LIMIT}
        onOffsetChange={setOffset}
        emptyLabel="No events match the filter."
      />
    </div>
  );
}
