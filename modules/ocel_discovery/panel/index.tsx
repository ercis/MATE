"use client";

import { useMemo, useRef, useState } from "react";
import { AlertTriangle } from "lucide-react";

import { cn } from "@/lib/cn";
import { formatNumber } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { EmptyState } from "@/components/empty-state";
import { CanvasLayoutSkeleton } from "@/components/visualizations/canvases/shared/canvas-skeleton";

import { ObjectGraphCanvas } from "./canvases/ObjectGraphCanvas";
import { OcdfgCanvas } from "./canvases/OcdfgCanvas";
import { OcpnCanvas } from "./canvases/OcpnCanvas";
import { MatrixHeatmap, type MatrixMetric } from "./MatrixHeatmap";
import { ObjectsSummary } from "./ObjectsSummary";
import {
  useActivityObjectTypes,
  useObjectsGraph,
  useObjectsSummary,
  useOcdfg,
  useOcelSummary,
  useOcpn,
  type ObjectGraphType,
  type OcdfgData,
} from "./queries";

type View = "overview" | "ocdfg" | "ocpn" | "object-graph" | "matrix" | "objects";

const VIEWS: { value: View; label: string }[] = [
  { value: "overview", label: "Overview" },
  { value: "ocdfg", label: "OC-DFG" },
  { value: "ocpn", label: "OC Petri net" },
  { value: "object-graph", label: "Object graph" },
  { value: "matrix", label: "Activity / type" },
  { value: "objects", label: "Objects" },
];

export function OcelDiscoveryPanel({ logId }: { logId: string; moduleId: string }) {
  const [view, setView] = useState<View>("overview");

  return (
    <div className="flex flex-col gap-4">
      <div
        role="tablist"
        aria-label="Object-centric discovery views"
        className="inline-flex w-fit items-center gap-1 rounded-lg bg-muted p-[3px]"
      >
        {VIEWS.map((v) => {
          const active = v.value === view;
          return (
            <button
              key={v.value}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => setView(v.value)}
              className={cn(
                "cursor-pointer whitespace-nowrap rounded-md px-3 py-1.5 text-sm font-medium transition-all",
                active
                  ? "bg-background text-foreground shadow-sm"
                  : "text-foreground/60 hover:text-foreground",
              )}
            >
              {v.label}
            </button>
          );
        })}
      </div>

      <div className="space-y-3">
        {view === "overview" && <OverviewTab logId={logId} />}
        {view === "ocdfg" && <OcdfgTab logId={logId} />}
        {view === "ocpn" && <OcpnTab logId={logId} />}
        {view === "object-graph" && <ObjectGraphTab logId={logId} />}
        {view === "matrix" && <MatrixTab logId={logId} />}
        {view === "objects" && <ObjectsTab logId={logId} />}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------
// Shared helpers
// --------------------------------------------------------------------------

function CanvasError({ message }: { message: string }) {
  return (
    <div className="h-[640px] w-full overflow-hidden rounded-xl border bg-card">
      <EmptyState icon={AlertTriangle} title="Nothing to show" description={message} />
    </div>
  );
}

function ObjectTypePicker({
  value,
  options,
  onChange,
}: {
  value: string | null;
  options: string[];
  onChange: (v: string) => void;
}) {
  return (
    <Select value={value ?? undefined} onValueChange={onChange}>
      <SelectTrigger className="h-8 w-[220px]">
        <SelectValue placeholder="Object type" />
      </SelectTrigger>
      <SelectContent>
        {options.map((t) => (
          <SelectItem key={t} value={t}>
            {t}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

/** Keeps the last non-undefined result so a canvas – and the settings popover
 *  inside it – stays mounted while the next relation is computed. */
function useSticky<T>(value: T | undefined): T | undefined {
  const ref = useRef<T | undefined>(undefined);
  if (value !== undefined) ref.current = value;
  return ref.current;
}

function KpiTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border/60 bg-muted/20 px-3 py-2">
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="mt-0.5 truncate text-lg font-semibold tabular-nums tracking-tight">{value}</div>
    </div>
  );
}

// --------------------------------------------------------------------------
// Overview
// --------------------------------------------------------------------------

function OverviewTab({ logId }: { logId: string }) {
  const summary = useOcelSummary(logId);
  const ocdfg = useOcdfg(logId);
  const [ot, setOt] = useState<string | null>(null);
  const activeOt = ot ?? ocdfg.data?.object_types[0] ?? null;

  const edges = useMemo(
    () => (ocdfg.data && activeOt ? ocdfg.data.edges.filter((e) => e.object_type === activeOt) : []),
    [ocdfg.data, activeOt],
  );
  const starts = useMemo(
    () =>
      ocdfg.data && activeOt
        ? ocdfg.data.start_activities.filter((a) => a.object_type === activeOt)
        : [],
    [ocdfg.data, activeOt],
  );
  const ends = useMemo(
    () =>
      ocdfg.data && activeOt
        ? ocdfg.data.end_activities.filter((a) => a.object_type === activeOt)
        : [],
    [ocdfg.data, activeOt],
  );

  if (summary.isLoading) return <Skeleton className="h-64 w-full" />;
  if (summary.isError || !summary.data)
    return <CanvasError message="Could not load the OCEL summary." />;

  const s = summary.data;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <KpiTile label="Object types" value={formatNumber(s.object_types.length)} />
        <KpiTile label="Objects" value={formatNumber(s.objects_count)} />
        <KpiTile label="Events" value={formatNumber(s.events_count)} />
        <KpiTile label="Activities" value={formatNumber(s.activities_count)} />
      </div>

      <div className="flex flex-wrap gap-1.5">
        {s.object_types.map((o) => (
          <Badge key={o.type} variant="secondary" className="gap-1.5">
            {o.type}
            <span className="tabular-nums text-muted-foreground">{formatNumber(o.count)}</span>
          </Badge>
        ))}
      </div>

      {ocdfg.data && ocdfg.data.object_types.length > 0 && (
        <div className="space-y-3">
          <div className="flex items-center justify-between gap-3">
            <h3 className="text-sm font-medium">Directly-follows</h3>
            <ObjectTypePicker value={activeOt} options={ocdfg.data.object_types} onChange={setOt} />
          </div>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <EdgeList edges={edges} />
            <div className="space-y-3">
              <ActivityList title="Start activities" items={starts} />
              <ActivityList title="End activities" items={ends} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function EdgeList({ edges }: { edges: OcdfgData["edges"] }) {
  if (edges.length === 0)
    return (
      <p className="py-6 text-center text-xs text-muted-foreground">
        No directly-follows edges for this object type.
      </p>
    );
  return (
    <ul className="space-y-1">
      {edges.map((e) => (
        <li
          key={`${e.source}→${e.target}`}
          className="flex items-center justify-between gap-2 rounded-md border border-border/60 bg-muted/20 px-2.5 py-1.5 text-xs"
        >
          <span className="truncate font-mono">
            {e.source} <span className="text-muted-foreground">→</span> {e.target}
          </span>
          <span className="shrink-0 tabular-nums text-muted-foreground">{formatNumber(e.count)}</span>
        </li>
      ))}
    </ul>
  );
}

function ActivityList({ title, items }: { title: string; items: OcdfgData["start_activities"] }) {
  return (
    <div>
      <h4 className="mb-1.5 text-xs font-medium text-muted-foreground">{title}</h4>
      {items.length === 0 ? (
        <p className="text-xs text-muted-foreground">None.</p>
      ) : (
        <ul className="space-y-1">
          {items.map((a) => (
            <li
              key={a.activity}
              className="flex items-center justify-between gap-2 rounded-md border border-border/60 bg-muted/20 px-2.5 py-1.5 text-xs"
            >
              <span className="truncate">{a.activity}</span>
              <span className="shrink-0 tabular-nums text-muted-foreground">
                {formatNumber(a.count)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------
// OC-DFG (visual graph)
// --------------------------------------------------------------------------

function OcdfgTab({ logId }: { logId: string }) {
  const { data, isLoading, isError, error } = useOcdfg(logId);

  if (isLoading) return <CanvasLayoutSkeleton />;
  if (isError || !data)
    return <CanvasError message={(error as Error)?.message ?? "Failed to load the OC-DFG."} />;
  if (data.object_types.length === 0)
    return <CanvasError message="This log has no object types." />;

  // No controls above the canvas – object type, measure and the performance
  // toggle live in the canvas settings popover (see `canvas-toolbar.tsx`).
  return <OcdfgCanvas data={data} />;
}

// --------------------------------------------------------------------------
// OC Petri net (visual graph)
// --------------------------------------------------------------------------

function OcpnTab({ logId }: { logId: string }) {
  const { data, isLoading, isError, error } = useOcpn(logId);

  if (isLoading) return <CanvasLayoutSkeleton />;
  if (isError || !data)
    return (
      <CanvasError message={(error as Error)?.message ?? "Failed to discover the Petri net."} />
    );
  if (data.object_types.length === 0)
    return <CanvasError message="This log has no object types." />;

  return <OcpnCanvas data={data} />;
}

// --------------------------------------------------------------------------
// Object interaction / relation graph (visual graph)
// --------------------------------------------------------------------------

function ObjectGraphTab({ logId }: { logId: string }) {
  const [graphType, setGraphType] = useState<ObjectGraphType>("object_interaction");
  const q = useObjectsGraph(logId, graphType);
  // Keep the previous graph on screen while the next relation is computed, so
  // the settings popover the user just switched in stays open.
  const shown = useSticky(q.data);

  if (q.isError)
    return (
      <CanvasError message={(q.error as Error)?.message ?? "Failed to discover the object graph."} />
    );
  if (!shown) return <CanvasLayoutSkeleton />;
  if (shown.object_types.length === 0)
    return <CanvasError message="This log has no object types." />;
  if (shown.edges.length === 0)
    return <CanvasError message="No relations of this kind between object types." />;

  return (
    <ObjectGraphCanvas
      data={shown}
      graphType={graphType}
      onGraphTypeChange={setGraphType}
      busy={q.isFetching}
    />
  );
}

// --------------------------------------------------------------------------
// Activity / object-type matrix
// --------------------------------------------------------------------------

function MatrixTab({ logId }: { logId: string }) {
  const { data, isLoading, isError } = useActivityObjectTypes(logId);
  const [metric, setMetric] = useState<MatrixMetric>("events");

  if (isLoading) return <Skeleton className="h-64 w-full" />;
  if (isError || !data) return <CanvasError message="Could not load the activity matrix." />;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground">
          How often each activity touches each object type.
        </p>
        <Select value={metric} onValueChange={(v) => setMetric(v as MatrixMetric)}>
          <SelectTrigger className="h-8 w-[150px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="events">Events</SelectItem>
            <SelectItem value="objects">Objects</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <div className="rounded-xl border bg-card p-3">
        <MatrixHeatmap data={data} metric={metric} />
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------
// Object lifecycle summary
// --------------------------------------------------------------------------

function ObjectsTab({ logId }: { logId: string }) {
  const { data, isLoading, isError } = useObjectsSummary(logId);

  if (isLoading) return <Skeleton className="h-64 w-full" />;
  if (isError || !data) return <CanvasError message="Could not load the object summary." />;

  return <ObjectsSummary data={data} />;
}
