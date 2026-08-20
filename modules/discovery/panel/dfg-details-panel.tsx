"use client";

import { ArrowDown, Play, Square, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { formatDuration, formatNumber } from "@/lib/format";

import type { DfgActivity, DfgData, DfgEdge } from "./types";

interface DfgDetailsPanelProps {
  data: DfgData;
  selectedNodeId?: string | null;
  selectedEdgeId?: string | null;
  onClose: () => void;
}

export function DfgDetailsPanel({
  data,
  selectedNodeId,
  selectedEdgeId,
  onClose,
}: DfgDetailsPanelProps) {
  const node =
    selectedNodeId != null ? data.activities.find((a) => a.id === selectedNodeId) ?? null : null;
  const edge =
    selectedEdgeId != null ? data.edges.find((e) => e.id === selectedEdgeId) ?? null : null;

  if (!node && !edge) return null;

  return (
    <aside
      className="absolute right-3 top-3 bottom-3 z-10 flex w-[400px] max-w-[calc(100vw-1.5rem)] flex-col overflow-hidden rounded-xl border bg-card/95 shadow-xl backdrop-blur"
      // Stop scroll/pan/zoom events from bleeding into the React Flow canvas
      // when the user is reading the panel.
      onWheelCapture={(e) => e.stopPropagation()}
      onPointerDownCapture={(e) => e.stopPropagation()}
    >
      <header className="flex items-center justify-between gap-2 border-b px-4 py-2.5">
        <div className="flex min-w-0 items-center gap-2">
          <Badge
            variant="outline"
            className="border-0 bg-muted text-[10px] font-medium uppercase tracking-wider"
          >
            {node ? "Activity" : "Connection"}
          </Badge>
          <h3 className="truncate text-sm font-semibold">
            {node ? node.label : edgeTitle(edge!, data)}
          </h3>
        </div>
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7 cursor-pointer shrink-0"
          onClick={onClose}
          aria-label="Close details"
        >
          <X className="h-3.5 w-3.5" />
        </Button>
      </header>

      <ScrollArea className="flex-1">
        <div className="px-4 py-3">
          {node ? (
            <NodeDetails activity={node} data={data} />
          ) : edge ? (
            <EdgeDetails edge={edge} data={data} />
          ) : null}
        </div>
      </ScrollArea>
    </aside>
  );
}

// --------------------------------------------------------------------------
// Node details
// --------------------------------------------------------------------------

function NodeDetails({ activity, data }: { activity: DfgActivity; data: DfgData }) {
  const totalEvents = data.activities.reduce((s, a) => s + a.frequency, 0);
  const startCount = data.start_activities[activity.id] ?? 0;
  const endCount = data.end_activities[activity.id] ?? 0;
  const totalCases = sumValues(data.start_activities);

  const incoming = data.edges.filter(
    (e) => e.target === activity.id && e.source !== activity.id,
  );
  const outgoing = data.edges.filter(
    (e) => e.source === activity.id && e.target !== activity.id,
  );
  const selfLoop = data.edges.find((e) => e.source === activity.id && e.target === activity.id);
  const incomingTotal = incoming.reduce((s, e) => s + e.frequency, 0);
  const outgoingTotal = outgoing.reduce((s, e) => s + e.frequency, 0);

  const topIncoming = topByFrequency(incoming, 3);
  const topOutgoing = topByFrequency(outgoing, 3);

  return (
    <div className="space-y-4">
      <Section title="Frequency">
        <Stat
          label="Events"
          value={formatNumber(activity.frequency)}
          hint={percent(activity.frequency, totalEvents)}
        />
        {selfLoop && (
          <Stat label="Self-loop" value={formatNumber(selfLoop.frequency)} />
        )}
      </Section>

      <Separator />

      <Section title="Role">
        {!startCount && !endCount && (
          <p className="text-xs text-muted-foreground">Intermediate activity.</p>
        )}
        {startCount > 0 && (
          <Stat
            label={
              <span className="inline-flex items-center gap-1.5">
                <Play className="h-3 w-3 fill-chart-2 text-chart-2" />
                Cases starting here
              </span>
            }
            value={formatNumber(startCount)}
            hint={percent(startCount, totalCases)}
          />
        )}
        {endCount > 0 && (
          <Stat
            label={
              <span className="inline-flex items-center gap-1.5">
                <Square className="h-3 w-3 fill-chart-1 text-chart-1" />
                Cases ending here
              </span>
            }
            value={formatNumber(endCount)}
            hint={percent(endCount, totalCases)}
          />
        )}
      </Section>

      <Separator />

      <Section title="Connections">
        <Stat
          label="Incoming"
          value={`${incoming.length} ${incoming.length === 1 ? "edge" : "edges"}`}
          hint={`${formatNumber(incomingTotal)} events`}
        />
        <Stat
          label="Outgoing"
          value={`${outgoing.length} ${outgoing.length === 1 ? "edge" : "edges"}`}
          hint={`${formatNumber(outgoingTotal)} events`}
        />
      </Section>

      {(topIncoming.length > 0 || topOutgoing.length > 0) && (
        <>
          <Separator />
          <Section title="Most-used edges">
            {topIncoming.length > 0 && (
              <div className="space-y-1">
                <div className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                  Incoming
                </div>
                {topIncoming.map((e) => (
                  <EdgeRow
                    key={e.id}
                    edge={e}
                    totalEvents={totalEvents}
                    otherLabel={labelFor(data, e.source)}
                  />
                ))}
              </div>
            )}
            {topOutgoing.length > 0 && (
              <div className="space-y-1">
                <div className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                  Outgoing
                </div>
                {topOutgoing.map((e) => (
                  <EdgeRow
                    key={e.id}
                    edge={e}
                    totalEvents={totalEvents}
                    otherLabel={labelFor(data, e.target)}
                  />
                ))}
              </div>
            )}
          </Section>
        </>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------
// Edge details
// --------------------------------------------------------------------------

function EdgeDetails({ edge, data }: { edge: DfgEdge; data: DfgData }) {
  const sourceActivity = data.activities.find((a) => a.id === edge.source);
  const targetActivity = data.activities.find((a) => a.id === edge.target);
  const totalTransitions = data.edges.reduce((s, e) => s + e.frequency, 0);
  const sourceFreq = sourceActivity?.frequency ?? 0;
  const targetFreq = targetActivity?.frequency ?? 0;

  return (
    <div className="space-y-4">
      <Section title="Path">
        <div className="rounded-lg border bg-muted/40 px-3 py-2.5 text-xs">
          <div className="flex items-baseline gap-2">
            <span className="w-12 shrink-0 text-[10px] uppercase tracking-wider text-muted-foreground">
              From
            </span>
            <span className="min-w-0 flex-1 break-words font-medium">
              {sourceActivity?.label ?? edge.source}
            </span>
          </div>
          <div className="my-1 ml-[3.25rem]">
            <ArrowDown className="h-3.5 w-3.5 text-muted-foreground" />
          </div>
          <div className="flex items-baseline gap-2">
            <span className="w-12 shrink-0 text-[10px] uppercase tracking-wider text-muted-foreground">
              To
            </span>
            <span className="min-w-0 flex-1 break-words font-medium">
              {targetActivity?.label ?? edge.target}
            </span>
          </div>
        </div>
        {edge.source === edge.target && (
          <p className="text-xs text-muted-foreground">Self-loop on this activity.</p>
        )}
      </Section>

      <Separator />

      <Section title="Frequency">
        <Stat
          label="Transitions"
          value={formatNumber(edge.frequency)}
          hint={percent(edge.frequency, totalTransitions)}
        />
        {sourceFreq > 0 && (
          <Stat
            label="Of source events"
            value={percent(edge.frequency, sourceFreq) ?? "–"}
            hint={`${formatNumber(sourceFreq)} total`}
          />
        )}
        {targetFreq > 0 && (
          <Stat
            label="Of target events"
            value={percent(edge.frequency, targetFreq) ?? "–"}
            hint={`${formatNumber(targetFreq)} total`}
          />
        )}
      </Section>

      {typeof edge.performance_seconds === "number" && (
        <>
          <Separator />
          <Section title="Duration">
            <Stat label="Mean transition time" value={formatDuration(edge.performance_seconds)} />
          </Section>
        </>
      )}

      {typeof edge.dependency === "number" && (
        <>
          <Separator />
          <Section title="Dependency (Heuristics)">
            <Stat label="Score" value={edge.dependency.toFixed(3)} />
            <p className="text-[11px] leading-snug text-muted-foreground">
              How strongly this transition is preferred over its reverse – closer to 1 means a
              dominant direction.
            </p>
          </Section>
        </>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------
// Layout helpers
// --------------------------------------------------------------------------

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2">
      <h4 className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        {title}
      </h4>
      <div className="space-y-2">{children}</div>
    </section>
  );
}

function Stat({
  label,
  value,
  hint,
}: {
  label: React.ReactNode;
  value: React.ReactNode;
  hint?: string | null;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-xs">
      <span className="min-w-0 flex-1 text-muted-foreground">{label}</span>
      <span className="flex min-w-0 flex-col items-end text-right">
        <span className="font-medium tabular-nums">{value}</span>
        {hint && (
          <span className="max-w-full truncate text-[10px] text-muted-foreground" title={hint}>
            {hint}
          </span>
        )}
      </span>
    </div>
  );
}

function EdgeRow({
  edge,
  totalEvents,
  otherLabel,
}: {
  edge: DfgEdge;
  totalEvents: number;
  otherLabel: string;
}) {
  return (
    <div className="flex items-baseline justify-between gap-2 text-xs">
      <span className="min-w-0 flex-1 truncate text-muted-foreground">{otherLabel}</span>
      <span className="font-medium tabular-nums">{formatNumber(edge.frequency)}</span>
      <span className="w-12 text-right text-[10px] text-muted-foreground">
        {percent(edge.frequency, totalEvents) ?? ""}
      </span>
    </div>
  );
}

function labelFor(data: DfgData, id: string): string {
  return data.activities.find((a) => a.id === id)?.label ?? id;
}

// --------------------------------------------------------------------------
// Pure helpers
// --------------------------------------------------------------------------

function edgeTitle(edge: DfgEdge, data: DfgData): string {
  const src = data.activities.find((a) => a.id === edge.source)?.label ?? edge.source;
  const tgt = data.activities.find((a) => a.id === edge.target)?.label ?? edge.target;
  return `${src} → ${tgt}`;
}

function topByFrequency(edges: DfgEdge[], n: number): DfgEdge[] {
  return [...edges].sort((a, b) => b.frequency - a.frequency).slice(0, n);
}

function sumValues(rec: Record<string, number>): number {
  return Object.values(rec).reduce((s, v) => s + v, 0);
}

function percent(part: number, total: number): string | null {
  if (!total || part < 0) return null;
  return `${((part / total) * 100).toFixed(1)}%`;
}
