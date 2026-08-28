"use client";

import { useState, type ReactNode } from "react";

import Link from "next/link";
import { Activity, ArrowDown, Gauge, GitBranch, Play, Square, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { DRILL_PARAMS, activityHref, modulePath } from "@/lib/dashboards/drill";
import { formatDuration, formatNumber } from "@/lib/format";

import { useDiscoveryScope } from "./discovery-settings-context";
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

  const title = node ? node.label : edgeTitle(edge!, data);

  return (
    // No wheel/pointer capture guards here: CanvasShell renders `overlay` as a
    // sibling of <ReactFlow>, so panel events never traverse the pane that owns
    // pan/zoom. Capturing pointerdown only starved the ScrollArea's own
    // scrollbar thumb, which needs it to start a drag.
    <aside className="absolute right-3 top-3 bottom-3 z-10 flex w-[480px] max-w-[calc(100vw-1.5rem)] flex-col overflow-hidden rounded-xl border bg-card/95 shadow-xl backdrop-blur">
      <header className="flex items-center justify-between gap-3 border-b px-5 py-3">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <Badge
            variant="outline"
            className="shrink-0 border-0 bg-muted text-[10px] font-medium uppercase tracking-wider"
          >
            {node ? "Activity" : "Connection"}
          </Badge>
          <h3 className="line-clamp-2 min-w-0 break-words text-base font-semibold" title={title}>
            {title}
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

      {/* `min-h-0` is load-bearing: without it the flex item's `min-height:auto`
          lets the ScrollArea grow past the panel, so the viewport never
          overflows and the content is simply clipped by the aside. The viewport
          override kills Radix's `display:table` inner wrapper, which otherwise
          shrink-wraps to a long activity label and overflows horizontally. */}
      <ScrollArea className="min-h-0 flex-1 [&_[data-slot=scroll-area-viewport]>div]:!block">
        <div className="px-5 py-4 text-sm">
          {node ? (
            <NodeDetails key={node.id} activity={node} data={data} />
          ) : edge ? (
            <EdgeDetails key={edge.id} edge={edge} data={data} />
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

  const eventShare = percent(activity.frequency, totalEvents);
  const startShare = percent(startCount, totalCases);
  const endShare = percent(endCount, totalCases);

  return (
    <div className="space-y-5">
      <Section title="Frequency">
        <div className="grid grid-cols-2 gap-2">
          <StatCard
            label="Events"
            value={formatNumber(activity.frequency)}
            hint={eventShare ? `${eventShare} of total events` : null}
          />
          {selfLoop && <StatCard label="Self-loop" value={formatNumber(selfLoop.frequency)} />}
        </div>
      </Section>

      <Separator />

      <Section title="Role">
        {!startCount && !endCount ? (
          <p className="text-sm text-muted-foreground">Intermediate activity.</p>
        ) : (
          <div className="grid grid-cols-2 gap-2">
            {startCount > 0 && (
              <StatCard
                icon={<Play className="h-3 w-3 fill-chart-2 text-chart-2" />}
                label="Cases starting here"
                value={formatNumber(startCount)}
                hint={startShare ? `${startShare} of cases` : null}
              />
            )}
            {endCount > 0 && (
              <StatCard
                icon={<Square className="h-3 w-3 fill-chart-1 text-chart-1" />}
                label="Cases ending here"
                value={formatNumber(endCount)}
                hint={endShare ? `${endShare} of cases` : null}
              />
            )}
          </div>
        )}
      </Section>

      <Separator />

      <Section title="Connections">
        <div className="grid grid-cols-2 gap-2">
          <StatCard
            label="Incoming"
            value={formatNumber(incoming.length)}
            hint={`${formatNumber(incomingTotal)} events`}
          />
          <StatCard
            label="Outgoing"
            value={formatNumber(outgoing.length)}
            hint={`${formatNumber(outgoingTotal)} events`}
          />
        </div>
      </Section>

      {(incoming.length > 0 || outgoing.length > 0) && (
        <>
          <Separator />
          <Section title="Top transitions">
            <TransitionList
              key={`in-${activity.id}`}
              heading="Incoming"
              edges={incoming}
              getLabel={(e) => labelFor(data, e.source)}
            />
            <TransitionList
              key={`out-${activity.id}`}
              heading="Outgoing"
              edges={outgoing}
              getLabel={(e) => labelFor(data, e.target)}
            />
          </Section>
        </>
      )}

      <Separator />

      <ExploreSection activityId={activity.id} />
    </div>
  );
}

/** Cross-view jumps for the selected activity – same targets as the node's
 *  right-click menu on the canvas. Real links (loading.tsx fires, cmd-click
 *  works); the activity travels via the shared drill vocabulary so the
 *  destination can preselect it. */
function ExploreSection({ activityId }: { activityId: string }) {
  const { logId } = useDiscoveryScope();
  const activityParam = encodeURIComponent(activityId);

  return (
    <Section title="Explore">
      <div className="grid gap-2">
        <Button asChild variant="outline" size="sm" className="cursor-pointer justify-start gap-2">
          <Link href={activityHref(logId, activityId)}>
            <Activity className="h-3.5 w-3.5 text-muted-foreground" />
            Open activity view
          </Link>
        </Button>
        <Button asChild variant="outline" size="sm" className="cursor-pointer justify-start gap-2">
          <Link href={`${modulePath(logId, "performance")}?${DRILL_PARAMS.activity}=${activityParam}`}>
            <Gauge className="h-3.5 w-3.5 text-muted-foreground" />
            View performance metrics
          </Link>
        </Button>
        <Button asChild variant="outline" size="sm" className="cursor-pointer justify-start gap-2">
          <Link href={`/processes/${logId}?tab=variants&${DRILL_PARAMS.activity}=${activityParam}`}>
            <GitBranch className="h-3.5 w-3.5 text-muted-foreground" />
            Show variants with this activity
          </Link>
        </Button>
      </div>
    </Section>
  );
}

function TransitionList({
  heading,
  edges,
  getLabel,
}: {
  heading: string;
  edges: DfgEdge[];
  getLabel: (edge: DfgEdge) => string;
}) {
  const [expanded, setExpanded] = useState(false);

  if (edges.length === 0) return null;

  const sorted = topByFrequency(edges, edges.length);
  const maxFrequency = sorted[0]?.frequency ?? 0;
  const visible = expanded ? sorted : sorted.slice(0, 5);

  return (
    <div className="space-y-2">
      <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
        {heading}
      </div>
      <div className="space-y-2.5">
        {visible.map((e) => {
          const label = getLabel(e);
          const share = maxFrequency > 0 ? (e.frequency / maxFrequency) * 100 : 0;
          return (
            <div key={e.id}>
              <div className="flex items-baseline justify-between gap-3">
                <span className="min-w-0 flex-1 truncate text-sm" title={label}>
                  {label}
                </span>
                <span className="shrink-0 text-sm font-medium tabular-nums">
                  {formatNumber(e.frequency)}
                </span>
              </div>
              <div className="mt-1 h-1 rounded-full bg-muted">
                <div className="h-1 rounded-full bg-primary/60" style={{ width: `${share}%` }} />
              </div>
            </div>
          );
        })}
      </div>
      {sorted.length > 5 && (
        <button
          type="button"
          className="text-xs text-muted-foreground hover:text-foreground cursor-pointer"
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? "Show less" : `Show all ${sorted.length}`}
        </button>
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
  const transitionShare = percent(edge.frequency, totalTransitions);

  return (
    <div className="space-y-5">
      <Section title="Path">
        <div className="rounded-lg border bg-muted/40 px-4 py-3">
          <div className="flex items-baseline gap-2">
            <span className="w-12 shrink-0 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
              From
            </span>
            <span className="min-w-0 flex-1 break-words text-sm font-medium">
              {sourceActivity?.label ?? edge.source}
            </span>
          </div>
          <div className="my-1.5 ml-[3.5rem]">
            <ArrowDown className="h-3.5 w-3.5 text-muted-foreground" />
          </div>
          <div className="flex items-baseline gap-2">
            <span className="w-12 shrink-0 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
              To
            </span>
            <span className="min-w-0 flex-1 break-words text-sm font-medium">
              {targetActivity?.label ?? edge.target}
            </span>
          </div>
        </div>
        {edge.source === edge.target && (
          <p className="text-sm text-muted-foreground">Self-loop on this activity.</p>
        )}
      </Section>

      <Separator />

      <Section title="Frequency">
        <div className="grid grid-cols-2 gap-2">
          <StatCard
            label="Transitions"
            value={formatNumber(edge.frequency)}
            hint={transitionShare ? `${transitionShare} of all transitions` : null}
          />
          {sourceFreq > 0 && (
            <StatCard
              label="Of source events"
              value={percent(edge.frequency, sourceFreq) ?? "–"}
              hint={`${formatNumber(sourceFreq)} total`}
            />
          )}
          {targetFreq > 0 && (
            <StatCard
              label="Of target events"
              value={percent(edge.frequency, targetFreq) ?? "–"}
              hint={`${formatNumber(targetFreq)} total`}
            />
          )}
        </div>
      </Section>

      {typeof edge.performance_seconds === "number" && (
        <>
          <Separator />
          <Section title="Duration">
            <div className="grid grid-cols-2 gap-2">
              <StatCard
                label="Mean transition time"
                value={formatDuration(edge.performance_seconds)}
              />
            </div>
          </Section>
        </>
      )}

      {typeof edge.dependency === "number" && (
        <>
          <Separator />
          <Section title="Dependency (Heuristics)">
            <div className="grid grid-cols-2 gap-2">
              <StatCard label="Score" value={edge.dependency.toFixed(3)} />
            </div>
            <p className="text-xs leading-snug text-muted-foreground">
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

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-2.5">
      <h4 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        {title}
      </h4>
      <div className="space-y-3">{children}</div>
    </section>
  );
}

function StatCard({
  label,
  value,
  hint,
  icon,
}: {
  label: string;
  value: ReactNode;
  hint?: string | null;
  icon?: ReactNode;
}) {
  return (
    <div className="rounded-lg border bg-muted/40 px-3 py-2.5">
      <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
        {icon}
        <span className="min-w-0">{label}</span>
      </div>
      <div className="mt-1 text-lg font-semibold tabular-nums leading-tight">{value}</div>
      {hint && <div className="mt-0.5 text-xs text-muted-foreground">{hint}</div>}
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
