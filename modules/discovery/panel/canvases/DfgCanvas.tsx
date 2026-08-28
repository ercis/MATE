"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  useEdgesState,
  useNodesState,
  type Edge,
  type EdgeMouseHandler,
  type Node,
  type NodeMouseHandler,
} from "@xyflow/react";
import { toast } from "sonner";

import { formatDuration, formatNumber } from "@/lib/format";

import { runAfterPaint } from "../after-paint";
import { celonisFlowLayout } from "../layout/celonis-flow";
import { ActivityNode, type ActivityNodeData } from "../nodes/activity-node";
import { TerminalNode, type TerminalNodeData } from "../nodes/terminal-node";
import { CelonisEdge } from "../edges/celonis-edge";
import { WaypointEdge } from "../edges/waypoint-edge";
import { RoutedEdge } from "../edges/routed-edge";
import { DfgCanvasSettings } from "../dfg-canvas-controls";
import { DfgNodeMenu, type DfgNodeMenuTarget } from "../dfg-node-menu";
import type {
  DfgData,
  DfgLayoutAlgorithm,
  DfgLayoutEdge,
  DfgLayoutRequest,
} from "../types";
import { useDfgServerLayout } from "../queries";
import { CanvasShell } from "@/components/visualizations/canvases/shared/canvas-shell";
import { CanvasLayoutSkeleton } from "@/components/visualizations/canvases/shared/canvas-skeleton";
import { computeDfgVisibility } from "../dfg-filter";
import {
  useDfgSettings,
  useDiscoveryScope,
  useGeneralSettings,
  useNodePositions,
  usePersistNodePositions,
  useResetPositions,
} from "../discovery-settings-context";

const nodeTypes = { activity: ActivityNode, terminal: TerminalNode } as const;
const edgeTypes = { celonis: CelonisEdge, waypoint: WaypointEdge, routed: RoutedEdge } as const;

/** Pseudo-node ids for the Process-flow terminals (never real activity ids —
 *  activity ids are the activity labels themselves). */
const START_ID = "__START__";
const END_ID = "__END__";
/** Terminal pill size — must match what celonisFlowLayout assumes. Wide
 *  enough that "PROCESS START" + case count render on one line. */
const TERMINAL_SIZE = { width: 184, height: 36 } as const;
/** Sizes measured from Celonis for the 1:1 clone mode. Height = their
 *  two-line card (59px) so long names always fit 2 clamped lines + count. */
const CELONIS_NODE_SIZE = { width: 220, height: 59 } as const;
const CELONIS_TERMINAL_SIZE = { width: 112, height: 43 } as const;

/** Celonis-style compact count: 965, 1K, 36.7K, 1.2M. */
function compactCount(n: number): string {
  if (n >= 1e6) return `${(n / 1e6).toFixed(n % 1e6 === 0 ? 0 : 1)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(n % 1000 === 0 ? 0 : 1)}K`;
  return String(n);
}

/** Duration of the node-position morph when a re-layout replaces an existing one. */
const LAYOUT_ANIMATION_MS = 420;

function easeInOutCubic(t: number): number {
  return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
}

interface DfgCanvasProps {
  data: DfgData;
  /** "frequency" → edges labelled with event counts; "performance" → mean duration. */
  metric?: "frequency" | "performance";
  /** Optional id of an activity to highlight. */
  highlightedActivityId?: string | null;
  /** Currently-selected element id (matches `selectionKind`). */
  selectedNodeId?: string | null;
  /** Currently-selected edge id (matches `selectionKind`). */
  selectedEdgeId?: string | null;
  /** Fired on click of a node, edge, or empty pane (passes `null` for the
   *  pane click). The parent owns the selection state so the details panel
   *  can render alongside the canvas. */
  onSelect?: (selection: { kind: "node" | "edge"; id: string } | null) => void;
  /** Optional overlay rendered on top of the canvas (e.g. details panel). */
  overlay?: ReactNode;
  /** Override the canvas wrapper sizing. Defaults to the panel's fixed-height
   *  shell; dashboard widgets pass `h-full w-full …` to fill their card. */
  shellClassName?: string;
}

export function DfgCanvas({
  data,
  metric = "frequency",
  highlightedActivityId,
  selectedNodeId,
  selectedEdgeId,
  onSelect,
  overlay,
  shellClassName,
}: DfgCanvasProps) {
  const { logId } = useDiscoveryScope();
  const general = useGeneralSettings();
  const [dfg] = useDfgSettings();
  const positions = useNodePositions("dfg");
  const persist = usePersistNodePositions("dfg");
  const resetPositions = useResetPositions();

  // Connectivity-aware Celonis-style filter (sliders + spanning floor) —
  // shared between the layout effect and the server-layout request.
  const filtered = useMemo(() => computeDfgVisibility(data, dfg), [data, dfg]);

  // Server-side layouts (Backbone / Sugiyama): the visible subgraph exists
  // only client-side, so it is POSTed to the discovery module, which returns
  // pixel coordinates + per-edge waypoints (cached by graph digest).
  const isServerLayout = dfg.layoutMode !== "celonis-classic";
  const layoutRequest = useMemo<DfgLayoutRequest | null>(() => {
    if (!isServerLayout) return null;
    const { visibleActivities, visibleEdges } = filtered;
    if (visibleActivities.length === 0) return null;
    const startSet = new Set(Object.keys(data.start_activities));
    const endSet = new Set(Object.keys(data.end_activities));
    const nodes = visibleActivities.map((a) => ({
      id: a.id,
      width: CELONIS_NODE_SIZE.width,
      height: CELONIS_NODE_SIZE.height,
    }));
    const edges: [string, string][] = visibleEdges.map((e) => [e.source, e.target]);
    // Same terminal-injection predicate as the render effect below: the
    // server pins these as the paper's artificial source/sink.
    const visibleStarts = visibleActivities.filter((a) => startSet.has(a.id));
    const visibleEnds = visibleActivities.filter((a) => endSet.has(a.id));
    let startId: string | null = null;
    let endId: string | null = null;
    if (visibleStarts.length > 0) {
      startId = START_ID;
      nodes.push({
        id: START_ID,
        width: CELONIS_TERMINAL_SIZE.width,
        height: CELONIS_TERMINAL_SIZE.height,
      });
      for (const a of visibleStarts) edges.push([START_ID, a.id]);
    }
    if (visibleEnds.length > 0) {
      endId = END_ID;
      nodes.push({
        id: END_ID,
        width: CELONIS_TERMINAL_SIZE.width,
        height: CELONIS_TERMINAL_SIZE.height,
      });
      for (const a of visibleEnds) edges.push([a.id, END_ID]);
    }
    return {
      algorithm: dfg.layoutMode as DfgLayoutAlgorithm,
      nodes,
      edges,
      start_id: startId,
      end_id: endId,
    };
  }, [isServerLayout, dfg.layoutMode, filtered, data]);

  const layoutQuery = useDfgServerLayout(logId, layoutRequest);
  const serverLayout = isServerLayout ? layoutQuery.data : undefined;
  const serverLayoutFailed = isServerLayout && layoutQuery.isError;

  useEffect(() => {
    if (serverLayoutFailed) {
      toast.error("Server layout failed — showing Process flow (Celonis) instead.");
    }
  }, [serverLayoutFailed]);

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [laid, setLaid] = useState(false);
  // Bumped when a layout-mode switch finishes animating → CanvasShell re-fits
  // the viewport to the new geometry (stale-bbox fits are what you'd get from
  // keying on the mode directly, since positions land after the animation).
  const [fitNonce, setFitNonce] = useState(0);
  const [menu, setMenu] = useState<DfgNodeMenuTarget | null>(null);

  // Latest committed nodes – animation start positions, without wiring the
  // node state into the layout effect's dependencies.
  const nodesRef = useRef<Node[]>([]);
  nodesRef.current = nodes;
  const animRef = useRef(0);
  // First layout is deferred past first paint (skeleton) so the synchronous
  // Celonis Sugiyama doesn't block it; re-layouts (mode/filter switches) run
  // inline so the existing morph animation keeps its same-tick timing.
  const didFirstLayout = useRef(false);
  const lastModeRef = useRef<typeof dfg.layoutMode | null>(null);
  // Committed (post-layout, post-merge) positions — baseline for deciding
  // whether a drag actually moved a node (see onNodeDragStop).
  const layoutPosRef = useRef<Map<string, { x: number; y: number }>>(new Map());

  useEffect(() => {
    let cancelled = false;
    let cancelAfterPaint: (() => void) | undefined;
    const maxFreq = data.activities.reduce((m, a) => Math.max(m, a.frequency), 1);
    const maxEdgeFreq = data.edges.reduce((m, e) => Math.max(m, e.frequency), 1);

    const startSet = new Set(Object.keys(data.start_activities));
    const endSet = new Set(Object.keys(data.end_activities));

    const { visibleActivities, visibleEdges } = filtered;

    // Which path renders this pass: the synchronous client-side Celonis clone,
    // or a server-computed layout (backbone / sugiyama). A failed or empty
    // server request falls back to the Celonis path so the map never blanks.
    const isCelonis = !isServerLayout || serverLayoutFailed || layoutRequest === null;
    // The node/edge SKIN stays the measured Celonis look in every mode.
    const isClassic = true;

    const activityNodes: Node<ActivityNodeData>[] = visibleActivities.map((a) => ({
      id: a.id,
      type: "activity",
      position: { x: 0, y: 0 },
      data: {
        // The Celonis skin does its own 2-line CSS clamp with ellipsis.
        label: a.label,
        frequency: a.frequency,
        isStart: startSet.has(a.id),
        isEnd: endSet.has(a.id),
        startCount: data.start_activities[a.id] ?? 0,
        endCount: data.end_activities[a.id] ?? 0,
        intensity:
          general.theme === "monochrome"
            ? 0
            : (a.frequency / Math.max(maxFreq, 1)) * general.colorIntensity,
        highlighted: a.id === highlightedActivityId,
        // Every mode is top-down: source handle bottom, target handle top.
        handleOrientation: "vertical" as const,
        ...(isClassic
          ? {
              variant: "celonis" as const,
              countLabel: compactCount(a.frequency),
              freqRatio: a.frequency / Math.max(maxFreq, 1),
            }
          : {}),
      },
    }));

    // Backbone v2 ships a finished path per edge; the other server layouts ship
    // waypoints the client splines itself.
    const edgeType = isCelonis
      ? ("celonis" as const)
      : dfg.layoutMode === "backbone-v2"
        ? ("routed" as const)
        : ("waypoint" as const);

    const dfgEdges: Edge[] = visibleEdges.map((e) => {
      // Edge label modes: explicit (no silent fall-through to count when
      // duration is selected but missing – show "–" so the user knows).
      let label: string | undefined;
      if (dfg.edgeLabel === "off") {
        label = undefined;
      } else if (dfg.edgeLabel === "duration" || metric === "performance") {
        label =
          typeof e.performance_seconds === "number"
            ? formatDuration(e.performance_seconds)
            : "–";
      } else {
        // count — the Celonis clone abbreviates like the original (# 36.7K).
        label = `# ${compactCount(e.frequency)}`;
      }

      return {
        id: e.id,
        source: e.source,
        target: e.target,
        label,
        labelStyle: { fill: "var(--muted-foreground)", fontSize: isClassic ? 11 : 10 },
        labelBgPadding: [4, 2] as [number, number],
        labelBgBorderRadius: isClassic ? 11 : 4,
        labelBgStyle: { fill: "var(--card)", stroke: "var(--border)", strokeWidth: 1 },
        type: edgeType,
        animated: false,
        style: {
          // Measured Celonis styling: uniform teal, width ∝ frequency
          // (ceil(12·f/max), min 1), no fading.
          stroke: "var(--dfg-edge, rgb(36, 148, 153))",
          strokeWidth: Math.max(1, Math.ceil((12 * e.frequency) / Math.max(maxEdgeFreq, 1))),
          opacity: 1,
        },
      };
    });

    // Process-flow terminals: pseudo-nodes bracketing the map (Celonis's
    // "Process start"/"Process end"), one dashed edge per visible start/end
    // activity. Injected in every mode — the server layouts pin them as the
    // artificial source/sink. Labels only in count mode — terminals have no
    // duration.
    const celonisNodes: Node[] = [...activityNodes];
    const celonisEdges: Edge[] = [...dfgEdges];
    {
      const terminalStyle = {
        stroke: "var(--dfg-edge, rgb(36, 148, 153))",
        strokeWidth: 1,
        opacity: 1,
      } as const;
      const visibleStarts = visibleActivities.filter((a) => startSet.has(a.id));
      const visibleEnds = visibleActivities.filter((a) => endSet.has(a.id));
      if (visibleStarts.length > 0) {
        const total = visibleStarts.reduce((s, a) => s + (data.start_activities[a.id] ?? 0), 0);
        celonisNodes.push({
          id: START_ID,
          type: "terminal",
          position: { x: 0, y: 0 },
          data: {
            kind: "start",
            caseCount: total,
            orientation: "vertical",
            ...(isClassic ? { variant: "celonis" as const } : {}),
          } satisfies TerminalNodeData,
        });
        for (const a of visibleStarts) {
          const c = data.start_activities[a.id] ?? 0;
          celonisEdges.push({
            id: `${START_ID}__${a.id}`,
            source: START_ID,
            target: a.id,
            type: edgeType,
            label:
              dfg.edgeLabel === "count" && metric !== "performance"
                ? isClassic
                  ? `# ${compactCount(c)}`
                  : formatNumber(c)
                : undefined,
            labelStyle: { fill: "var(--muted-foreground)", fontSize: isClassic ? 11 : 10 },
            ...(isClassic ? { labelBgBorderRadius: 11 } : {}),
            style: terminalStyle,
          });
        }
      }
      if (visibleEnds.length > 0) {
        const total = visibleEnds.reduce((s, a) => s + (data.end_activities[a.id] ?? 0), 0);
        celonisNodes.push({
          id: END_ID,
          type: "terminal",
          position: { x: 0, y: 0 },
          data: {
            kind: "end",
            caseCount: total,
            orientation: "vertical",
            ...(isClassic ? { variant: "celonis" as const } : {}),
          } satisfies TerminalNodeData,
        });
        for (const a of visibleEnds) {
          const c = data.end_activities[a.id] ?? 0;
          celonisEdges.push({
            id: `${a.id}__${END_ID}`,
            source: a.id,
            target: END_ID,
            type: edgeType,
            label:
              dfg.edgeLabel === "count" && metric !== "performance"
                ? isClassic
                  ? `# ${compactCount(c)}`
                  : formatNumber(c)
                : undefined,
            labelStyle: { fill: "var(--muted-foreground)", fontSize: isClassic ? 11 : 10 },
            ...(isClassic ? { labelBgBorderRadius: 11 } : {}),
            style: terminalStyle,
          });
        }
      }
    }


    // Within-layer tie-breaker. Prefer real temporal order (mean_trace_position
    // from the discovery serializer v3+): activities that occur earlier in
    // traces float to the top/left of their layer. Falls back to negative
    // frequency for older cached payloads where the field is missing – both
    // are deterministic and meaningful, the temporal one is just truer.
    const positionByActivity = new Map<string, number>();
    const frequencyByActivity = new Map<string, number>();
    for (const a of visibleActivities) {
      if (typeof a.mean_trace_position === "number") {
        positionByActivity.set(a.id, a.mean_trace_position);
      }
      frequencyByActivity.set(a.id, a.frequency);
    }
    const hasTemporal = positionByActivity.size > 0;

    const nodeSize = CELONIS_NODE_SIZE;
    const termSize = CELONIS_TERMINAL_SIZE;

    // Pin every node to the exact pixel size the layout assumed: activity
    // cards are content-sized by default, so their real handle x would drift
    // from the layout's columns and trip the edge renderers' drag-fallbacks.
    for (const n of celonisNodes) {
      n.style = {
        ...(n.style ?? {}),
        width: n.type === "terminal" ? termSize.width : nodeSize.width,
        height: n.type === "terminal" ? termSize.height : nodeSize.height,
      };
    }

    /** Merge persisted (dragged) positions, then commit – animating the node
     *  movement when this re-layout replaces an already-rendered one, so a
     *  layout switch morphs instead of snapping. `modeChanged` is decided
     *  HERE, not in the effect body: server layouts apply asynchronously, so
     *  the mode ref may only advance once a layout for the new mode actually
     *  lands (else the refit nonce never bumps). */
    const apply = (result: { nodes: Node<ActivityNodeData>[]; edges: Edge[] }) => {
      if (cancelled) return;
      const modeChanged = lastModeRef.current !== null && lastModeRef.current !== dfg.layoutMode;
      lastModeRef.current = dfg.layoutMode;
      const merged = result.nodes.map((n) => {
        const p = positions[n.id];
        return p ? { ...n, position: p } : n;
      });

      window.cancelAnimationFrame(animRef.current);
      setEdges(result.edges);

      const prev = new Map(nodesRef.current.map((n) => [n.id, n.position] as const));
      const moves = merged.some((n) => {
        const p = prev.get(n.id);
        return p && (Math.abs(p.x - n.position.x) > 0.5 || Math.abs(p.y - n.position.y) > 0.5);
      });

      const finish = () => {
        setNodes(merged);
        layoutPosRef.current = new Map(merged.map((n) => [n.id, n.position]));
        setLaid(true);
        // Refit on mode switches AND on the very first layout: ReactFlow's
        // mount-time fitView can frame before a synchronous layout's node
        // dimensions settle, leaving the initial viewport misaligned.
        if (modeChanged || !laid) setFitNonce((v) => v + 1);
      };

      if (!laid || !moves) {
        finish();
        return;
      }

      const from = merged.map((n) => prev.get(n.id) ?? n.position);
      const start = performance.now();
      const step = (now: number) => {
        if (cancelled) return;
        const t = Math.min(1, (now - start) / LAYOUT_ANIMATION_MS);
        if (t >= 1) {
          finish();
          return;
        }
        const k = easeInOutCubic(t);
        setNodes(
          merged.map((n, i) => ({
            ...n,
            position: {
              x: from[i]!.x + (n.position.x - from[i]!.x) * k,
              y: from[i]!.y + (n.position.y - from[i]!.y) * k,
            },
          })),
        );
        animRef.current = window.requestAnimationFrame(step);
      };
      animRef.current = window.requestAnimationFrame(step);
    };

    if (isCelonis) {
      // Process flow (Celonis look): custom Sugiyama — DP max-frequency spine
      // pinned to the center column, loop-backs in outside lanes, terminal
      // pseudo-nodes. Synchronous, so the morph animation runs same-tick.
      const maxStartCount = Math.max(1, ...Object.values(data.start_activities));
      const significantStarts = new Set(
        Object.entries(data.start_activities)
          .filter(([, c]) => c >= maxStartCount * 0.1)
          .map(([id]) => id),
      );
      const edgeFreqMap = new Map<string, number>();
      for (const e of visibleEdges) edgeFreqMap.set(`${e.source} ${e.target}`, e.frequency);
      const runLayout = () => {
        didFirstLayout.current = true;
        apply(
          celonisFlowLayout(celonisNodes, celonisEdges, {
            nodeSize,
            terminalSize: termSize,
            edgeFrequency: (s, t) => edgeFreqMap.get(`${s} ${t}`) ?? 0,
            frequencyByNode: (id) => frequencyByActivity.get(id) ?? 0,
            // Layout anchoring uses only SIGNIFICANT starts (≥10% of the max
            // start count): a marginal start like SEPSIS' Leucocytes (18 of
            // 1050 cases) must not pin a mid-process activity to the top row.
            // The Process-start terminal still connects to ALL visible starts.
            startActivityIds: significantStarts,
            endActivityIds: endSet,
            rankByNode: (id) => positionByActivity.get(id),
            startTerminalId: START_ID,
            endTerminalId: END_ID,
            routing: "celonis",
            classic: true,
          }) as { nodes: Node<ActivityNodeData>[]; edges: Edge[] },
        );
      };
      // First open: yield so the CanvasLayoutSkeleton paints before the heavy
      // synchronous Sugiyama blocks the thread. Re-layouts run inline so the
      // node-position morph keeps its existing same-tick start.
      if (didFirstLayout.current) runLayout();
      else cancelAfterPaint = runAfterPaint(runLayout);
    } else if (serverLayout && !layoutQuery.isPlaceholderData) {
      // Server layout (backbone / sugiyama). Placeholder frames (previous
      // graph's geometry kept alive by keepPreviousData) are NOT applied —
      // the previous frame is already on screen; applying only the fresh
      // response makes mode switches refit and slider tweaks morph.
      const covers = celonisNodes.every(
        (n) => serverLayout.x[n.id] !== undefined && serverLayout.y[n.id] !== undefined,
      );
      if (covers) {
        const positioned = celonisNodes.map((n) => ({
          ...n,
          position: { x: serverLayout.x[n.id]!, y: serverLayout.y[n.id]! },
        }));
        const routeByPair = new Map<string, DfgLayoutEdge>();
        for (const routed of serverLayout.edges) {
          routeByPair.set(`${routed.source} ${routed.target}`, routed);
        }
        const routedEdges = celonisEdges.map((e) => {
          const route = routeByPair.get(`${e.source} ${e.target}`);
          return {
            ...e,
            data: {
              ...(e.data ?? {}),
              waypoints: route?.waypoints ?? [],
              selfLoop: route?.self_loop ?? e.source === e.target,
              bidirectional: route?.bidirectional ?? false,
              backEdge: route?.back_edge ?? false,
              // Backbone v2 only — the router's finished geometry. Undefined
              // for the other algorithms, which makes RoutedEdge degrade to
              // the same bezier it uses for a dragged node.
              path: route?.path,
              polyline: route?.polyline,
              arrow: route?.arrow ?? null,
              labelAt: route?.label_at ?? null,
              // Layout-time node positions: the waypoint edge compares them
              // against the live rects to detect drags (then falls back to a
              // plain bezier, mirroring the Celonis edge's contract).
              expected: {
                sx: serverLayout.x[e.source]!,
                sy: serverLayout.y[e.source]!,
                tx: serverLayout.x[e.target]!,
                ty: serverLayout.y[e.target]!,
              },
            },
            // Paper convention: upward (loop-back) edges read red.
            style: route?.back_edge
              ? { ...(e.style ?? {}), stroke: "var(--dfg-edge-back, rgb(198, 78, 60))" }
              : e.style,
          };
        });
        didFirstLayout.current = true;
        apply({ nodes: positioned as Node<ActivityNodeData>[], edges: routedEdges });
      }
      // Fresh data that doesn't cover the current subgraph (a stale cache
      // entry racing a filter change): keep the previous frame; the query
      // refetches and this effect re-runs when the right response lands.
    }

    return () => {
      cancelled = true;
      cancelAfterPaint?.();
      window.cancelAnimationFrame(animRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    data,
    metric,
    highlightedActivityId,
    general.colorIntensity,
    general.theme,
    dfg.activitiesShown,
    dfg.connectionsShown,
    dfg.hideSelfLoops,
    dfg.edgeTopPercent,
    dfg.edgeLabel,
    dfg.layoutMode,
    filtered,
    // Server-layout inputs: re-apply when a response lands, flips to error,
    // or stops being a keepPreviousData placeholder.
    serverLayout,
    serverLayoutFailed,
    layoutQuery.isPlaceholderData,
    layoutRequest,
    // Re-layout when persisted node positions change — this is what makes
    // "Reset layout" morph the graph back WITHOUT a reload (the store slice
    // identity changes on reset and after every real drag; both re-merge).
    positions,
  ]);

  const onNodeDragStop = useCallback<NodeMouseHandler>(
    (_, node) => {
      // Persist only real movement. A plain click (or a click that lands
      // mid-layout-animation) can end a zero-distance "drag"; persisting it
      // would freeze the node at a junk position that then survives reloads
      // via the server-synced store. Belt to canvas-shell's nodeDragThreshold.
      const base = layoutPosRef.current.get(node.id);
      // No committed layout yet (drag fired during load/morph) → nothing
      // meaningful to persist; and a no-op "drag" (≤2px) is a click.
      if (!base) return;
      if (
        Math.abs(base.x - node.position.x) <= 2 &&
        Math.abs(base.y - node.position.y) <= 2
      ) {
        return;
      }
      persist({ [node.id]: { x: node.position.x, y: node.position.y } });
    },
    [persist],
  );

  const onNodeClick = useCallback<NodeMouseHandler>(
    (_, node) => {
      if (node.type === "terminal") return; // pseudo-nodes have no details
      setMenu(null);
      onSelect?.({ kind: "node", id: node.id });
    },
    [onSelect],
  );
  const onEdgeClick = useCallback<EdgeMouseHandler>(
    (_, edge) => {
      setMenu(null);
      onSelect?.({ kind: "edge", id: edge.id });
    },
    [onSelect],
  );
  const onPaneClick = useCallback(() => {
    setMenu(null);
    onSelect?.(null);
  }, [onSelect]);

  // Right-click on an activity node → cross-view jump menu (performance,
  // variants). preventDefault suppresses the browser context menu.
  const onNodeContextMenu = useCallback<NodeMouseHandler>((event, node) => {
    event.preventDefault();
    if (node.type === "terminal") return;
    const label = (node.data as ActivityNodeData | undefined)?.label;
    setMenu({
      activityId: node.id,
      label: typeof label === "string" && label.length > 0 ? label : node.id,
      x: event.clientX,
      y: event.clientY,
    });
  }, []);

  // Multi-selection via rubber-band: clear the single-node details panel.
  const onSelectionChange = useCallback(
    ({ nodes: selected }: { nodes: typeof nodes; edges: typeof edges }) => {
      if (selected.length > 1) onSelect?.(null);
    },
    [onSelect],
  );

  // Ensure the externally-controlled selectedNodeId is visually selected.
  // Don't clear other nodes' selection so rubber-band multi-select is preserved.
  const decoratedNodes = nodes.map((n) =>
    n.id === selectedNodeId && !n.selected ? { ...n, selected: true } : n,
  );
  const decoratedEdges = edges.map((e) =>
    e.id === selectedEdgeId && !e.selected ? { ...e, selected: true } : e,
  );

  if (!laid) return <CanvasLayoutSkeleton />;
  return (
    <CanvasShell
      className={shellClassName}
      nodes={decoratedNodes}
      edges={decoratedEdges}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      fitViewKey={`${data.kind}-${data.activities.length}-${fitNonce}`}
      miniMap={general.showMinimap}
      showGrid={general.showGrid}
      settings={<DfgCanvasSettings data={data} />}
      settingsTourId="discovery-filters"
      onReset={() => resetPositions()}
      resetDescription="All dragged node positions for this module on this log will be discarded and the auto-layout will be reapplied. This cannot be undone."
      overlay={
        <>
          {menu ? (
            <DfgNodeMenu target={menu} logId={logId} onClose={() => setMenu(null)} />
          ) : null}
          {overlay}
        </>
      }
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onNodeDragStop={onNodeDragStop}
      onNodeClick={onNodeClick}
      onNodeContextMenu={onNodeContextMenu}
      onEdgeClick={onEdgeClick}
      onPaneClick={onPaneClick}
      onSelectionChange={onSelectionChange}
    />
  );
}
