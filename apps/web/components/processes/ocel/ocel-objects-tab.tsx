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
import { useOcelObjects, useOcelObjectTypes } from "@/lib/queries";
import { OcelDataTable } from "./ocel-data-table";

const LIMIT = 100;
const ALL = "__all__";

export function OcelObjectsTab({ logId }: { logId: string }) {
  const [offset, setOffset] = useState(0);
  const [objectType, setObjectType] = useState<string>(ALL);
  const [q, setQ] = useState("");

  const { data: types } = useOcelObjectTypes(logId);
  const { data, isLoading, isError, error } = useOcelObjects(logId, {
    offset,
    limit: LIMIT,
    object_type: objectType === ALL ? undefined : objectType,
    q: q.trim() || undefined,
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
                {t.type} ({t.count})
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <div className="relative min-w-[220px] max-w-md flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Filter by object id…"
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
        emptyLabel="No objects match the filter."
      />
    </div>
  );
}
