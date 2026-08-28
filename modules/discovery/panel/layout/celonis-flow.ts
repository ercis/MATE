import { Position, type Edge, type Node } from "@xyflow/react";

/**
 * "Process flow" layout — the Celonis-style DFG view, fully custom Sugiyama
 * variant tuned for loop-heavy directly-follows graphs. No ELK: at DFG scale
 * (≤ ~50 visible nodes) O(V·E) passes are microseconds, and the look needs
 * hard guarantees a generic layered engine can't express as options:
 *
 *   - the maximum-frequency START→END path (NOT a greedy walk — see
 *     `findSpine`) is pinned to a straight center column,
 *   - long edges and loop-backs route through reserved vertical lanes OUTSIDE
 *     the node columns instead of sweeping diagonally across the map,
 *   - bidirectional pairs render as two parallel offset curves,
 *   - Process start/end terminal pseudo-nodes bracket the flow.
 *
 * Passes: (1) temporal orientation → forward DAG, (2) DP spine, (3) longest-
 * path layering, (4) barycenter slot assignment, (5) edge classification +
 * geometry, (6) label collision. Deterministic throughout (stable tie-breaks
 * on frequency then id), synchronous — the canvas's morph animation and the
 * settings-driven re-layouts stay same-tick.
 */

export interface CelonisFlowOptions {
  /** Activity card size (the canvas passes its vertical default {220, 60}). */
  nodeSize: { width: number; height: number };
  /** Terminal pill size. */
  terminalSize?: { width: number; height: number };
  /** Horizontal distance between column centers. */
  columnWidth?: number;
  /** Vertical gap between layers (row pitch = nodeSize.height + layerGap). */
  layerGap?: number;
  /** Gap between stacked outside lanes. */
  laneGap?: number;
  edgeFrequency: (source: string, target: string) => number;
  frequencyByNode: (id: string) => number;
  startActivityIds: Set<string>;
  endActivityIds: Set<string>;
  /** Temporal rank ∈ [0,1] (mean_trace_position). May be 0 — check with
   *  `typeof`, never truthiness. Undefined on pre-v3 payloads. */
  rankByNode: (id: string) => number | undefined;
  /** Ids of the injected terminal pseudo-nodes, when present in `nodes`. */
  startTerminalId?: string;
  endTerminalId?: string;
  /** Edge routing style. "curved" (default) = vertical-tangent beziers +
   *  side-entry lanes. "ortho" = rounded-orthogonal doglegs. "celonis" =
   *  1:1 clone of Celonis Process Explorer geometry (see
   *  celonis-reference.json, measured from a live instance): near-straight
   *  chord cubics, smooth lane arcs with angled entries, arrowheads whose
   *  line stops 12px short of the node. */
  routing?: "curved" | "ortho" | "celonis";
  /** Classic Celonis styling: renderer draws small arrowheads and skips the
   *  animated flow overlay (stroke styling handled by the canvas). */
  classic?: boolean;
}

export type CelonisEdgeKind = "forward" | "twin" | "lane" | "selfloop" | "terminal";

export interface CelonisEdgeGeometry extends Record<string, unknown> {
  kind: CelonisEdgeKind;
  /** Path waypoints. Beziers carry exactly [source, target] anchors; lanes
   *  carry the full rounded-polyline waypoint list; self-loops carry
   *  [start, apex, end]. */
  points: { x: number; y: number }[];
  /** Collision-adjusted label anchor. Renderer falls back to the midpoint. */
  labelPos?: { x: number; y: number };
  /** Sideways bend (px, signed) of a bezier's control points — set on long
   *  center-channel edges so they don't coincide with the straight chain. */
  bow?: number;
  /** Terminal edge styling hint (fainter stroke; the flow overlay still
   *  animates like every other edge). */
  dashed?: boolean;
  /** Classic mode: renderer adds a small arrowhead and skips the animation. */
  classic?: boolean;
  /** Pre-built SVG path ("celonis" routing) — renderer uses it verbatim. */
  path?: string;
  /** Arrowhead spec ("celonis" routing): tip position, tangent angle (deg),
   *  scale of the measured Celonis triangle (0.5 thin … 1 thick). */
  arrow?: { x: number; y: number; angle: number; scale: number };
  /** Live routing plan ("celonis" routing): the renderer rebuilds the path
   *  every render from the CURRENT node rects via `buildCelonisEdgePath`, so
   *  dragged nodes keep fully-routed Celonis edges instead of falling back. */
  plan?: CelonisRoutePlan;
  /** xyflow handle coordinates at layout time — the renderer compares them
   *  with the live handle props to detect node drags (then falls back to a
   *  plain bezier, same contract as elk-spline-edge). */
  expected: { sx: number; sy: number; tx: number; ty: number };
}

interface Box {
  cx: number;
  top: number;
  bottom: number;
  left: number;
  right: number;
  cy: number;
}

// ── Live Celonis routing ─────────────────────────────────────────────────────

export interface CelonisRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface CelonisRoutePlan {
  kind: "chord" | "lane" | "selfloop";
  /** Exit-point offset from the source face center (the layout's fan
   *  spread), clamped into the face at build time. For self-loops this is
   *  the y-offset along the right face. */
  exitOffsetX: number;
  /** Fixed outside-lane x (lane kind). */
  laneX?: number;
  /** Signed bulge for same-column twin pairs (±14). */
  twinBow?: number;
  /** Label position along the path (0..1) and offsets. */
  labelT?: number;
  labelDX?: number;
  arrowScale: number;
}

const EXACT_ARROW_GAP = 12;
const EXACT_ARC = 70;

const clampNum = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/** Entry point on the target rect for a ray from `from` toward the rect
 *  center — hits whichever face the approach crosses first (top, bottom,
 *  left or right), clamped off the corners. Outgoing stays top/bottom-only;
 *  incoming may use all four sides, exactly like Celonis. */
function entryOnRect(tgt: CelonisRect, from: { x: number; y: number }) {
  const cx = tgt.x + tgt.w / 2;
  const cy = tgt.y + tgt.h / 2;
  const dx = cx - from.x;
  const dy = cy - from.y;
  const uSide = dx !== 0 ? tgt.w / 2 / Math.abs(dx) : Infinity;
  const uHoriz = dy !== 0 ? tgt.h / 2 / Math.abs(dy) : Infinity;
  const u = Math.min(uSide, uHoriz, 1);
  let x = cx - dx * u;
  let y = cy - dy * u;
  if (uHoriz <= uSide) {
    x = clampNum(x, tgt.x + 10, tgt.x + tgt.w - 10);
    y = dy > 0 ? tgt.y : tgt.y + tgt.h;
  } else {
    y = clampNum(y, tgt.y + 8, tgt.y + tgt.h - 8);
    x = dx > 0 ? tgt.x : tgt.x + tgt.w;
  }
  const len = Math.hypot(dx, dy) || 1;
  return { x, y, ux: dx / len, uy: dy / len };
}

/**
 * Build the Celonis edge geometry from LIVE node rects. Pure — called by the
 * renderer on every render (and once at layout time for the initial label).
 */
export function buildCelonisEdgePath(
  src: CelonisRect,
  tgt: CelonisRect,
  plan: CelonisRoutePlan,
): { path: string; arrow: { x: number; y: number; angle: number; scale: number }; labelPoint: { x: number; y: number } } {
  const scx = src.x + src.w / 2;
  const scy = src.y + src.h / 2;
  const tcy = tgt.y + tgt.h / 2;

  if (plan.kind === "selfloop") {
    const y0 = clampNum(scy + plan.exitOffsetX, src.y + 10, src.y + src.h - 10);
    const right = src.x + src.w;
    const path =
      `M ${right} ${y0 - 10} ` +
      `C ${right + 44} ${y0 - 24}, ${right + 54} ${y0 - 12}, ${right + 54} ${y0} ` +
      `C ${right + 54} ${y0 + 12}, ${right + 44} ${y0 + 24}, ${right + EXACT_ARROW_GAP} ${y0 + 10}`;
    return {
      path,
      arrow: { x: right, y: y0 + 10, angle: 180, scale: plan.arrowScale },
      labelPoint: { x: right + 58, y: y0 },
    };
  }

  const goingDown = tcy >= scy;
  const exitY = goingDown ? src.y + src.h : src.y;
  const exitX = clampNum(scx + plan.exitOffsetX, src.x + 10, src.x + src.w - 10);
  const dirOut = goingDown ? 1 : -1;

  if (plan.kind === "lane" && plan.laneX !== undefined) {
    const laneX = plan.laneX;
    // Approach the target from the lane: laterally when the lane runs beside
    // it, from above/below when the lane passes over/under its column.
    const lateral = Math.abs(laneX - (tgt.x + tgt.w / 2)) > tgt.w / 2 + 24;
    const runEndY = lateral
      ? tcy
      : tcy >= scy
        ? tgt.y - EXACT_ARC
        : tgt.y + tgt.h + EXACT_ARC;
    // Clamp the straight run so short lanes can't invert into a cusp: the
    // run start never overshoots past the run end.
    let runStartY = exitY + dirOut * EXACT_ARC;
    runStartY = dirOut > 0 ? Math.min(runStartY, runEndY) : Math.max(runStartY, runEndY);
    // Exit-arc control strength shrinks with the available room (no wobble
    // on tight lanes).
    const exitRoom = Math.abs(runStartY - exitY);
    const c1y = exitY + dirOut * Math.min(38, exitRoom * 0.7);
    const c2y = exitY + dirOut * Math.min(22, exitRoom * 0.4);

    const A = { x: laneX, y: runEndY };
    const e = entryOnRect(tgt, A);
    const lineEndX = e.x - e.ux * EXACT_ARROW_GAP;
    const lineEndY = e.y - e.uy * EXACT_ARROW_GAP;
    // Stop the straight run BEFORE the entry height and turn through a
    // vertical-tangent cubic — an L ending exactly at the entry height meets
    // the horizontal entry curve in a hard 90° corner (the "not round"
    // kinks). dirRun falls back to the exit direction on degenerate runs.
    const dirRun = Math.sign(runEndY - runStartY) || dirOut;
    let preEntryY = runEndY - dirRun * 36;
    // Keep the pre-entry point between run start and run end.
    preEntryY = dirRun > 0 ? Math.max(runStartY, Math.min(preEntryY, runEndY)) : Math.min(runStartY, Math.max(preEntryY, runEndY));
    const entryDist = Math.hypot(lineEndX - laneX, lineEndY - preEntryY);
    const ek = clampNum(0.4 * entryDist, 16, 60);
    const path =
      `M ${exitX} ${exitY} ` +
      `C ${exitX} ${c1y}, ${laneX} ${c2y}, ${laneX} ${runStartY} ` +
      `L ${laneX} ${preEntryY} ` +
      `C ${laneX} ${preEntryY + dirRun * 24}, ${lineEndX - e.ux * ek} ${lineEndY - e.uy * ek}, ${lineEndX} ${lineEndY}`;
    return {
      path,
      arrow: { x: e.x, y: e.y, angle: (Math.atan2(e.uy, e.ux) * 180) / Math.PI, scale: plan.arrowScale },
      labelPoint: { x: laneX + (plan.labelDX ?? 0), y: (runStartY + preEntryY) / 2 },
    };
  }

  // Chord / vertical.
  const eFrom = { x: exitX, y: exitY };
  const e = entryOnRect(tgt, eFrom);
  const lineEndX = e.x - e.ux * EXACT_ARROW_GAP;
  const lineEndY = e.y - e.uy * EXACT_ARROW_GAP;
  const dx = lineEndX - exitX;
  const dy = lineEndY - exitY;
  let path: string;
  if (Math.abs(dx) < 0.5) {
    const bow = plan.twinBow ?? 0;
    path = `M ${exitX} ${exitY} C ${exitX + bow} ${exitY + 0.28 * dy}, ${exitX + bow} ${exitY + 0.61 * dy}, ${lineEndX} ${lineEndY}`;
  } else {
    // Celonis S-curve: LEAVE the source along the face normal (vertical exit
    // tangent), ARRIVE along the approach direction into the entry face —
    // never a straight chord.
    const dist = Math.hypot(dx, dy);
    const k = clampNum(0.35 * dist, 20, 70);
    const c1x = exitX;
    const c1y = exitY + dirOut * k;
    const c2x = lineEndX - e.ux * k;
    const c2y = lineEndY - e.uy * k;
    path = `M ${exitX} ${exitY} C ${c1x} ${c1y}, ${c2x} ${c2y}, ${lineEndX} ${lineEndY}`;
  }
  const t = plan.labelT ?? 0.5;
  return {
    path,
    arrow: { x: e.x, y: e.y, angle: (Math.atan2(e.uy, e.ux) * 180) / Math.PI, scale: plan.arrowScale },
    labelPoint: { x: exitX + dx * t + (plan.labelDX ?? 0), y: exitY + dy * t },
  };
}

/**
 * Greedy-free spine: maximum-total-frequency path from a start to an end over
 * the forward DAG, via DP in topological (R) order. Exported for tests.
 */
export function findSpine(
  order: string[], // topological order of the forward DAG
  fwdIn: Map<string, { from: string; f: number }[]>,
  fwdOut: Map<string, { to: string; f: number }[]>,
  sources: Set<string>,
  sinks: Set<string>,
  frequencyByNode: (id: string) => number,
): string[] {
  if (order.length === 0) return [];

  const dp = new Map<string, number>();
  const prev = new Map<string, string>();
  for (const v of order) dp.set(v, sources.has(v) ? 0 : -Infinity);

  for (const v of order) {
    let best = dp.get(v)!;
    let bestPrev: string | undefined;
    let bestEdgeF = -1;
    for (const { from, f } of fwdIn.get(v) ?? []) {
      const du = dp.get(from)!;
      if (du === -Infinity) continue;
      const cand = du + f;
      if (
        cand > best ||
        (cand === best && bestPrev !== undefined && (f > bestEdgeF || (f === bestEdgeF && du > dp.get(bestPrev)!)))
      ) {
        best = cand;
        bestPrev = from;
        bestEdgeF = f;
      }
    }
    if (bestPrev !== undefined) {
      dp.set(v, best);
      prev.set(v, bestPrev);
    }
  }

  // Best reachable sink; fall back to the globally best reachable node.
  const pick = (candidates: Iterable<string>): string | null => {
    let bestId: string | null = null;
    let bestDp = -Infinity;
    for (const id of candidates) {
      const d = dp.get(id);
      if (d === undefined || d === -Infinity) continue;
      if (
        d > bestDp ||
        (d === bestDp &&
          bestId !== null &&
          (frequencyByNode(id) > frequencyByNode(bestId) ||
            (frequencyByNode(id) === frequencyByNode(bestId) && id < bestId)))
      ) {
        bestDp = d;
        bestId = id;
      }
    }
    return bestId;
  };

  const target = pick(sinks) ?? pick(order);
  if (target === null) return [];

  const path: string[] = [];
  let cur: string | undefined = target;
  while (cur !== undefined) {
    path.push(cur);
    cur = prev.get(cur);
  }
  path.reverse();
  return path;
}

export function celonisFlowLayout<
  TN extends Record<string, unknown>,
  TE extends Record<string, unknown>,
>(
  nodes: Node<TN>[],
  edges: Edge<TE>[],
  opts: CelonisFlowOptions,
): { nodes: Node<TN>[]; edges: Edge<TE>[] } {
  if (nodes.length === 0) return { nodes, edges };

  const nodeW = opts.nodeSize.width;
  const nodeH = opts.nodeSize.height;
  const termW = opts.terminalSize?.width ?? 184;
  const termH = opts.terminalSize?.height ?? 36;
  const columnWidth = opts.columnWidth ?? nodeW + 60;
  const layerGap = opts.layerGap ?? 90;
  const laneGap = opts.laneGap ?? 28;
  const pitch = nodeH + layerGap;

  const terminalIds = new Set(
    [opts.startTerminalId, opts.endTerminalId].filter((x): x is string => typeof x === "string"),
  );
  const isTerminal = (id: string) => terminalIds.has(id);

  const activityIds = nodes.map((n) => n.id).filter((id) => !isTerminal(id));
  const activitySet = new Set(activityIds);

  // ── Pass 1: orientation — strict total order R over activities ────────────

  const rankOf = (id: string): number | undefined => {
    const r = opts.rankByNode(id);
    // NOTE: 0 is a legitimate rank (the very first activity) — typeof check.
    return typeof r === "number" && !Number.isNaN(r) ? r : undefined;
  };
  const anyRank = activityIds.some((id) => rankOf(id) !== undefined);

  // Interior (activity→activity, non-self-loop) edges only.
  const interior = edges.filter(
    (e) =>
      e.source !== e.target && activitySet.has(e.source) && activitySet.has(e.target),
  );

  let orderIds: string[];
  if (anyRank) {
    orderIds = [...activityIds].sort((a, b) => {
      const ra = rankOf(a);
      const rb = rankOf(b);
      if (ra !== undefined && rb !== undefined && ra !== rb) return ra - rb;
      if (ra !== undefined && rb === undefined) return -1;
      if (ra === undefined && rb !== undefined) return 1;
      const df = opts.frequencyByNode(b) - opts.frequencyByNode(a);
      if (df !== 0) return df;
      return a < b ? -1 : 1;
    });
  } else {
    // Pre-v3 fallback: heavier direction of each 2-cycle is candidate-forward;
    // iterative DFS marks stack-edges as back-edges; R = topological order.
    const cand = new Map<string, string[]>();
    for (const id of activityIds) cand.set(id, []);
    const seen = new Set<string>();
    for (const e of interior) {
      const key = e.source < e.target ? `${e.source}\u0000${e.target}` : `${e.target}\u0000${e.source}`;
      if (seen.has(key)) continue;
      seen.add(key);
      const fAB = opts.edgeFrequency(e.source, e.target);
      const fBA = opts.edgeFrequency(e.target, e.source);
      const [a, b] = e.source < e.target ? [e.source, e.target] : [e.target, e.source];
      const forward =
        fAB === 0 && fBA === 0
          ? [e.source, e.target]
          : opts.edgeFrequency(a, b) >= opts.edgeFrequency(b, a)
            ? [a, b]
            : [b, a];
      cand.get(forward[0]!)!.push(forward[1]!);
    }
    const roots = [
      ...[...opts.startActivityIds].filter((id) => activitySet.has(id)),
      ...[...activityIds].sort((a, b) => opts.frequencyByNode(b) - opts.frequencyByNode(a)),
    ];
    const color = new Map<string, 0 | 1 | 2>(); // 0 white, 1 on stack, 2 done
    for (const id of activityIds) color.set(id, 0);
    const topo: string[] = [];
    for (const root of roots) {
      if (color.get(root) !== 0) continue;
      const stack: { id: string; i: number }[] = [{ id: root, i: 0 }];
      color.set(root, 1);
      while (stack.length > 0) {
        const frame = stack[stack.length - 1]!;
        const outs = cand.get(frame.id) ?? [];
        if (frame.i < outs.length) {
          const next = outs[frame.i++]!;
          if (color.get(next) === 0) {
            color.set(next, 1);
            stack.push({ id: next, i: 0 });
          }
          // color 1 → back-edge (dropped from the DAG), color 2 → cross edge.
        } else {
          color.set(frame.id, 2);
          topo.push(frame.id);
          stack.pop();
        }
      }
    }
    topo.reverse();
    orderIds = topo;
  }

  const R = new Map<string, number>();
  orderIds.forEach((id, i) => R.set(id, i));

  const fwdOut = new Map<string, { to: string; f: number }[]>();
  const fwdIn = new Map<string, { from: string; f: number }[]>();
  for (const id of activityIds) {
    fwdOut.set(id, []);
    fwdIn.set(id, []);
  }
  for (const e of interior) {
    const f = opts.edgeFrequency(e.source, e.target);
    if (R.get(e.source)! < R.get(e.target)!) {
      fwdOut.get(e.source)!.push({ to: e.target, f });
      fwdIn.get(e.target)!.push({ from: e.source, f });
    }
    // else: loop-back — excluded from the DAG, classified in pass 5.
  }

  // ── Pass 2: spine ──────────────────────────────────────────────────────────

  const visibleStarts = new Set([...opts.startActivityIds].filter((id) => activitySet.has(id)));
  const visibleEnds = new Set([...opts.endActivityIds].filter((id) => activitySet.has(id)));

  let sources = visibleStarts;
  if (sources.size === 0) {
    sources = new Set(orderIds.filter((id) => (fwdIn.get(id) ?? []).length === 0));
  }
  if (sources.size === 0 && orderIds.length > 0) {
    // Degenerate: every node has forward in-edges (impossible in a DAG with
    // nodes, but guard anyway) — seed with the max-frequency node.
    const seed = [...orderIds].sort((a, b) => opts.frequencyByNode(b) - opts.frequencyByNode(a))[0]!;
    sources = new Set([seed]);
  }
  let sinks = visibleEnds;
  if (sinks.size === 0) {
    sinks = new Set(orderIds.filter((id) => (fwdOut.get(id) ?? []).length === 0));
  }

  const spine = findSpine(orderIds, fwdIn, fwdOut, sources, sinks, opts.frequencyByNode);
  const spineSet = new Set(spine);
  const spineNext = new Map<string, string>();
  for (let i = 0; i + 1 < spine.length; i++) spineNext.set(spine[i]!, spine[i + 1]!);

  // ── Pass 3: layering (longest path on the forward DAG) ────────────────────

  const L = new Map<string, number>();
  const hasFwd = (id: string) =>
    (fwdIn.get(id) ?? []).length > 0 || (fwdOut.get(id) ?? []).length > 0;

  // Only TRUE process starts seed layer 0. Aggressive connection filtering
  // can strip a mid-process node's forward in-edges; treating every
  // in-degree-0 node as a source dumped such nodes (Leucocytes on SEPSIS at
  // 11/10) at the very top. Nodes unreachable from the seeds instead adopt a
  // layer near their TEMPORAL position (R order) below.
  const visibleStartsL = new Set(
    [...opts.startActivityIds].filter((id) => activitySet.has(id) && hasFwd(id)),
  );
  const layerSeeds =
    visibleStartsL.size > 0
      ? visibleStartsL
      : new Set(orderIds.filter((id) => hasFwd(id) && (fwdIn.get(id) ?? []).length === 0));

  for (const v of orderIds) {
    if (!hasFwd(v)) continue; // isolated — assigned below
    if (layerSeeds.has(v)) {
      L.set(v, 0);
      continue;
    }
    let l: number | undefined;
    for (const { from } of fwdIn.get(v) ?? []) {
      const lu = L.get(from);
      if (lu !== undefined && (l === undefined || lu + 1 > l)) l = lu + 1;
    }
    if (l !== undefined) L.set(v, l);
  }

  // Unreachable-but-connected nodes: place right after their R-predecessor
  // (temporal neighbor), falling back to the next defined neighbor.
  for (let i = 0; i < orderIds.length; i++) {
    const v = orderIds[i]!;
    if (L.has(v) || !hasFwd(v)) continue;
    let adopted: number | undefined;
    for (let d = 1; d < orderIds.length; d++) {
      const beforeId = i - d >= 0 ? orderIds[i - d]! : undefined;
      if (beforeId !== undefined && L.has(beforeId)) {
        adopted = L.get(beforeId)! + 1;
        break;
      }
      const afterId = i + d < orderIds.length ? orderIds[i + d]! : undefined;
      if (afterId !== undefined && L.has(afterId)) {
        adopted = Math.max(0, L.get(afterId)! - 1);
        break;
      }
    }
    L.set(v, adopted ?? 0);
  }

  // Relaxation sweeps: adopted floors may violate edge constraints — push
  // forward targets below their sources again (2 passes settle at DFG scale).
  for (let pass = 0; pass < 2; pass++) {
    for (const v of orderIds) {
      if (!L.has(v)) continue;
      let l = L.get(v)!;
      for (const { from } of fwdIn.get(v) ?? []) {
        const lu = L.get(from);
        if (lu !== undefined && lu + 1 > l) l = lu + 1;
      }
      L.set(v, l);
    }
  }
  // Isolated nodes adopt the R-nearest layered node's layer (prefer earlier).
  for (let i = 0; i < orderIds.length; i++) {
    const v = orderIds[i]!;
    if (L.has(v)) continue;
    let adopted: number | undefined;
    for (let d = 1; d < orderIds.length; d++) {
      const before = i - d >= 0 ? L.get(orderIds[i - d]!) : undefined;
      if (before !== undefined) {
        adopted = before;
        break;
      }
      const after = i + d < orderIds.length ? L.get(orderIds[i + d]!) : undefined;
      if (after !== undefined) {
        adopted = after;
        break;
      }
    }
    L.set(v, adopted ?? i); // no forward edges anywhere → one node per layer
  }

  // Compact away empty layers.
  const usedLayers = [...new Set([...L.values()])].sort((a, b) => a - b);
  const layerRemap = new Map<number, number>();
  usedLayers.forEach((l, i) => layerRemap.set(l, i));
  for (const [id, l] of L) L.set(id, layerRemap.get(l)!);
  const activityLayerCount = usedLayers.length;

  // Terminals bracket the activity layers.
  const hasStartTerminal = opts.startTerminalId !== undefined && nodes.some((n) => n.id === opts.startTerminalId);
  const hasEndTerminal = opts.endTerminalId !== undefined && nodes.some((n) => n.id === opts.endTerminalId);
  const layerShift = hasStartTerminal ? 1 : 0;
  const layerOf = (id: string): number => {
    if (hasStartTerminal && id === opts.startTerminalId) return 0;
    if (hasEndTerminal && id === opts.endTerminalId) return activityLayerCount + layerShift;
    return (L.get(id) ?? 0) + layerShift;
  };

  // ── Pass 4: slots (signed columns, spine + terminals pinned to 0) ─────────

  const slot = new Map<string, number>();
  for (const id of spine) slot.set(id, 0);
  if (hasStartTerminal) slot.set(opts.startTerminalId!, 0);
  if (hasEndTerminal) slot.set(opts.endTerminalId!, 0);

  // Neighbor map over ALL interior edges (both orientations count for
  // barycenters) + terminal edges.
  const neighbors = new Map<string, string[]>();
  for (const id of activityIds) neighbors.set(id, []);
  for (const e of interior) {
    neighbors.get(e.source)!.push(e.target);
    neighbors.get(e.target)!.push(e.source);
  }
  for (const e of edges) {
    if (isTerminal(e.source) && activitySet.has(e.target)) neighbors.get(e.target)!.push(e.source);
    if (isTerminal(e.target) && activitySet.has(e.source)) neighbors.get(e.source)!.push(e.target);
  }

  const layers: string[][] = [];
  for (const id of activityIds) {
    const l = layerOf(id);
    (layers[l] ??= []).push(id);
  }

  let leftCount = 0;
  let rightCount = 0;

  const assignLayer = (layerNodes: string[], occupied: Set<number>) => {
    const bary = new Map<string, number | undefined>();
    for (const id of layerNodes) {
      const known = (neighbors.get(id) ?? [])
        .map((nb) => slot.get(nb))
        .filter((s): s is number => s !== undefined);
      bary.set(id, known.length > 0 ? known.reduce((a, b) => a + b, 0) / known.length : undefined);
    }
    const ordered = [...layerNodes].sort((a, b) => {
      const ba = bary.get(a);
      const bb = bary.get(b);
      if (ba !== undefined && bb !== undefined && ba !== bb) return ba - bb;
      if (ba !== undefined && bb === undefined) return -1;
      if (ba === undefined && bb !== undefined) return 1;
      const df = opts.frequencyByNode(b) - opts.frequencyByNode(a);
      if (df !== 0) return df;
      return a < b ? -1 : 1;
    });
    for (const id of ordered) {
      const b = bary.get(id);
      let side: 1 | -1;
      if (b !== undefined && b < 0) side = -1;
      else if (b !== undefined && b > 0) side = 1;
      else side = leftCount < rightCount ? -1 : 1; // emptier side, tie → right
      let s = side;
      while (occupied.has(s)) s += side;
      occupied.add(s);
      slot.set(id, s);
      if (s < 0) leftCount++;
      else rightCount++;
    }
  };

  // Down sweep, then one up-sweep refinement with full neighbor knowledge.
  for (const layerNodes of layers) {
    if (!layerNodes) continue;
    const offSpine = layerNodes.filter((id) => !spineSet.has(id));
    assignLayer(offSpine, new Set([0]));
  }
  for (let l = layers.length - 1; l >= 0; l--) {
    const layerNodes = layers[l];
    if (!layerNodes) continue;
    const offSpine = layerNodes.filter((id) => !spineSet.has(id));
    for (const id of offSpine) {
      const s = slot.get(id)!;
      if (s < 0) leftCount--;
      else rightCount--;
      slot.delete(id);
    }
    assignLayer(offSpine, new Set([0]));
  }

  // ── Coordinates ────────────────────────────────────────────────────────────

  const box = new Map<string, Box>();
  const positioned = nodes.map((n) => {
    const term = isTerminal(n.id);
    const w = term ? termW : nodeW;
    const h = term ? termH : nodeH;
    const cx = (slot.get(n.id) ?? 0) * columnWidth;
    const top = layerOf(n.id) * pitch + (nodeH - h) / 2;
    box.set(n.id, { cx, top, bottom: top + h, left: cx - w / 2, right: cx + w / 2, cy: top + h / 2 });
    return {
      ...n,
      position: { x: cx - w / 2, y: top },
      sourcePosition: Position.Bottom,
      targetPosition: Position.Top,
    };
  });

  // ── Pass 5: edge classification + geometry ─────────────────────────────────

  // Length-prefixed pair key: unambiguous even though activity ids are free
  // text (no control characters involved).
  const pairKey = (a: string, b: string) => `${a.length}:${a}${b}`;
  const visibleEdgeKeys = new Set(edges.map((e) => pairKey(e.source, e.target)));
  const spanOf = (e: Edge<TE>) => Math.abs(layerOf(e.target) - layerOf(e.source));
  const isSpineChainEdge = (e: Edge<TE>) =>
    (spineNext.get(e.source) === e.target) ||
    // Terminal edges hugging the spine ends stay in the center channel.
    (hasStartTerminal && e.source === opts.startTerminalId && spine.length > 0 && e.target === spine[0]) ||
    (hasEndTerminal && e.target === opts.endTerminalId && spine.length > 0 && e.source === spine[spine.length - 1]);

  interface LaneRequest {
    edge: Edge<TE>;
    side: 1 | -1;
    span: number;
    lo: number;
    hi: number;
    laneX: number;
  }
  interface BezierRequest {
    edge: Edge<TE>;
    kind: "forward" | "twin" | "terminal";
    downward: boolean;
    span: number;
    lo: number;
    hi: number;
    /** Sideways bend of the cubic's control points — de-overlaps long
     *  center-channel edges from the straight span-1 chain beneath them. */
    bow: number;
  }

  const laneRequests: LaneRequest[] = [];
  let bezierRequests: BezierRequest[] = [];
  const selfLoops: Edge<TE>[] = [];
  const geoByEdgeId = new Map<string, CelonisEdgeGeometry>();

  const expectedOf = (e: Edge<TE>): CelonisEdgeGeometry["expected"] => {
    const s = box.get(e.source)!;
    const t = box.get(e.target)!;
    return { sx: s.cx, sy: s.bottom, tx: t.cx, ty: t.top };
  };

  for (const e of edges) {
    const s = box.get(e.source);
    const t = box.get(e.target);
    if (!s || !t) continue;

    const terminal = isTerminal(e.source) || isTerminal(e.target);

    if (e.source === e.target) {
      selfLoops.push(e);
      continue;
    }

    const span = spanOf(e);
    const twin = !terminal && span === 1 && visibleEdgeKeys.has(pairKey(e.target, e.source));
    const downward = layerOf(e.target) > layerOf(e.source);

    if (span === 1 || isSpineChainEdge(e)) {
      bezierRequests.push({
        edge: e,
        kind: twin ? "twin" : terminal ? "terminal" : "forward",
        downward,
        span,
        lo: Math.min(layerOf(e.source), layerOf(e.target)),
        hi: Math.max(layerOf(e.source), layerOf(e.target)),
        bow: 0,
      });
      continue;
    }

    // Same-layer (span 0) or long off-spine edges: outside lane.
    const sideSum = (slot.get(e.source) ?? 0) + (slot.get(e.target) ?? 0);
    const side: 1 | -1 = sideSum < 0 ? -1 : 1;
    laneRequests.push({
      edge: e,
      side,
      span,
      lo: Math.min(layerOf(e.source), layerOf(e.target)),
      hi: Math.max(layerOf(e.source), layerOf(e.target)),
      laneX: 0,
    });
  }

  // Parallel over crossing: two channel edges of one layer gap whose endpoint
  // orders flip (A→Y vs B→X) would form a shallow X mid-channel that no anchor
  // assignment can undo. Reroute the weaker edge of each inverted pair through
  // the outside lanes instead. Opposite-direction edges between the SAME two
  // nodes are exempt — corridor ordering (below) renders those as hugging
  // parallels, which is the preferred look.
  {
    const byGap = new Map<number, BezierRequest[]>();
    for (const r of bezierRequests) {
      if (r.span >= 2) continue; // bowed center-channel edges stay put
      const list = byGap.get(r.lo);
      if (list) list.push(r);
      else byGap.set(r.lo, [r]);
    }
    const toLane = new Set<string>();
    for (const group of byGap.values()) {
      const freqs = group
        .map((r) => opts.edgeFrequency(r.edge.source, r.edge.target))
        .sort((a, b) => a - b);
      const median = freqs[Math.floor(freqs.length / 2)] ?? 0;
      for (let i = 0; i < group.length; i++) {
        for (let j = i + 1; j < group.length; j++) {
          const a = group[i]!;
          const b = group[j]!;
          if (a.edge.source === b.edge.target && a.edge.target === b.edge.source) continue;
          const ds = box.get(a.edge.source)!.cx - box.get(b.edge.source)!.cx;
          const dt = box.get(a.edge.target)!.cx - box.get(b.edge.target)!.cx;
          if (ds * dt >= 0) continue; // consistent order → corridor ordering parallelizes
          const fa = opts.edgeFrequency(a.edge.source, a.edge.target);
          const fb = opts.edgeFrequency(b.edge.source, b.edge.target);
          const loser = fa <= fb ? a : b;
          if (isSpineChainEdge(loser.edge)) continue; // never exile the spine
          const lf = opts.edgeFrequency(loser.edge.source, loser.edge.target);
          if (lf > median) continue; // don't exile major flows
          toLane.add(loser.edge.id);
        }
      }
    }
    if (toLane.size > 0) {
      for (const r of bezierRequests) {
        if (!toLane.has(r.edge.id)) continue;
        const sideSum = (slot.get(r.edge.source) ?? 0) + (slot.get(r.edge.target) ?? 0);
        laneRequests.push({
          edge: r.edge,
          side: sideSum < 0 ? -1 : 1,
          span: r.span,
          lo: r.lo,
          hi: r.hi,
          laneX: 0,
        });
      }
      bezierRequests = bezierRequests.filter((r) => !toLane.has(r.edge.id));
    }
  }

  // Lane allocation per side: span ascending → short arcs hug the columns.
  let maxRightSlot = 0;
  let maxLeftSlot = 0;
  for (const sVal of slot.values()) {
    if (sVal > maxRightSlot) maxRightSlot = sVal;
    if (sVal < maxLeftSlot) maxLeftSlot = sVal;
  }

  const laneCountBySide = new Map<1 | -1, number>([[1, 0], [-1, 0]]);
  for (const side of [1, -1] as const) {
    const reqs = laneRequests
      .filter((r) => r.side === side)
      .sort((a, b) => {
        if (a.span !== b.span) return a.span - b.span;
        const fa = opts.edgeFrequency(a.edge.source, a.edge.target);
        const fb = opts.edgeFrequency(b.edge.source, b.edge.target);
        if (fa !== fb) return fb - fa;
        return a.edge.id < b.edge.id ? -1 : 1;
      });
    const lanes: { lo: number; hi: number }[][] = [];
    const boundary =
      side === 1
        ? (maxRightSlot + 0.5) * columnWidth
        : (maxLeftSlot - 0.5) * columnWidth;

    for (const r of reqs) {
      let laneIdx = lanes.findIndex((iv) => iv.every((x) => r.hi < x.lo || r.lo > x.hi));
      if (laneIdx === -1) {
        laneIdx = lanes.length;
        lanes.push([]);
      }
      lanes[laneIdx]!.push({ lo: r.lo, hi: r.hi });
      r.laneX = boundary + side * laneGap * (laneIdx + 1);
    }
    laneCountBySide.set(side, lanes.length);
  }

  // Center-channel bows: long (span ≥ 2) center beziers would otherwise run
  // exactly on top of the straight span-1 chain — bend them sideways, stacked
  // by the same interval-scheduling used for the outside lanes. Bowed to the
  // emptier side so they don't fight the lane stack.
  {
    const bowSide: 1 | -1 =
      (laneCountBySide.get(-1) ?? 0) < (laneCountBySide.get(1) ?? 0) ? -1 : 1;
    const cands = bezierRequests
      .filter((r) => r.span >= 2)
      .sort((a, b) => (a.span !== b.span ? a.span - b.span : a.edge.id < b.edge.id ? -1 : 1));
    const bowLanes: { lo: number; hi: number }[][] = [];
    for (const r of cands) {
      let k = bowLanes.findIndex((iv) => iv.every((x) => r.hi < x.lo || r.lo > x.hi));
      if (k === -1) {
        k = bowLanes.length;
        bowLanes.push([]);
      }
      bowLanes[k]!.push({ lo: r.lo, hi: r.hi });
      r.bow = bowSide * (24 + 14 * k);
    }
  }

  // Corridor order: one global left→right order per layer gap. Every face's
  // anchors follow it, so any two edges sharing a gap keep the SAME relative
  // order at both ends — vertical-tangent cubics with matching endpoint order
  // cannot cross, the bundle reads as parallel curves. Opposite-direction
  // edges between the same node pair tie on the midline and split by source
  // x, landing on adjacent corridor slots → hugging parallels.
  const corridorIndex = new Map<string, number>();
  {
    const byGap = new Map<number, BezierRequest[]>();
    for (const r of bezierRequests) {
      if (r.bow !== 0) continue; // bowed edges anchor at the face's bow-side edge
      const list = byGap.get(r.lo);
      if (list) list.push(r);
      else byGap.set(r.lo, [r]);
    }
    for (const group of byGap.values()) {
      group.sort((a, b) => {
        const ma = box.get(a.edge.source)!.cx + box.get(a.edge.target)!.cx;
        const mb = box.get(b.edge.source)!.cx + box.get(b.edge.target)!.cx;
        if (ma !== mb) return ma - mb;
        const sa = box.get(a.edge.source)!.cx;
        const sb = box.get(b.edge.source)!.cx;
        if (sa !== sb) return sa - sb;
        return a.edge.id < b.edge.id ? -1 : 1;
      });
      group.forEach((r, i) => corridorIndex.set(r.edge.id, i));
    }
  }

  // ── Anchor distribution ────────────────────────────────────────────────────
  // Spread each node's edge endpoints along the face they use instead of
  // funnelling everything through one center point. Horizontal faces order by
  // the gap's corridor index (crossing-free by construction); side faces by
  // vertical direction then lane depth.

  const ortho = opts.routing === "ortho";
  const exact = opts.routing === "celonis";
  const classicFlag = (opts.classic === true || exact) || undefined;

  // "celonis" routing helpers — constants measured from the live product
  // (celonis-reference.json): the visible line stops 12px short of the node
  // border, the arrow triangle fills the gap; lane arcs blend over ~70px.
  const ARROW_GAP = 12;
  const ARC = 70;
  const arrowScale = (f: number, fMax: number) => (f / Math.max(fMax, 1) < 0.2 ? 0.5 : 1);
  let exactMaxFreq = 1;
  if (exact) {
    for (const e of edges) {
      if (e.source === e.target) continue;
      const f = opts.edgeFrequency(e.source, e.target);
      if (f > exactMaxFreq) exactMaxFreq = f;
    }
  }

  interface FaceItem {
    edgeId: string;
    endpoint: "s" | "t";
    k1: number;
    k2: number;
    tie: string;
  }
  const faceMap = new Map<string, FaceItem[]>();
  const pushFace = (nodeId: string, face: "bottom" | "top" | "left" | "right", item: FaceItem) => {
    const key = `${nodeId}|${face}`;
    const list = faceMap.get(key);
    if (list) list.push(item);
    else faceMap.set(key, [item]);
  };

  for (const r of bezierRequests) {
    const e = r.edge;
    const s = box.get(e.source)!;
    const t = box.get(e.target)!;
    // Corridor index keys BOTH endpoints of an edge identically → same
    // relative order on every face of the gap. Bowed edges (no corridor
    // entry) anchor toward their bow side, clear of the bundle.
    const ci = corridorIndex.get(e.id);
    const k1 = ci !== undefined ? ci : r.bow > 0 ? 1e6 : -1e6;
    if (r.downward) {
      pushFace(e.source, "bottom", { edgeId: e.id, endpoint: "s", k1, k2: t.cx + r.bow, tie: e.id });
      pushFace(e.target, "top", { edgeId: e.id, endpoint: "t", k1, k2: s.cx + r.bow, tie: e.id });
    } else {
      pushFace(e.source, "top", { edgeId: e.id, endpoint: "s", k1, k2: t.cx + r.bow, tie: e.id });
      pushFace(e.target, "bottom", { edgeId: e.id, endpoint: "t", k1, k2: s.cx + r.bow, tie: e.id });
    }
  }
  for (const r of laneRequests) {
    const e = r.edge;
    const s = box.get(e.source)!;
    const t = box.get(e.target)!;
    if (!ortho && !exact) {
      const face = r.side === 1 ? "right" : "left";
      // Upward-heading endpoints sit in the upper part of the face, downward
      // in the lower; within a group, inner lanes first.
      pushFace(e.source, face, { edgeId: e.id, endpoint: "s", k1: t.cy < s.cy ? 0 : 1, k2: r.laneX * r.side, tie: e.id });
      pushFace(e.target, face, { edgeId: e.id, endpoint: "t", k1: s.cy < t.cy ? 0 : 1, k2: r.laneX * r.side, tie: e.id });
    } else {
      // Ortho routing (Celonis): no side entries. Forward/same-layer lanes
      // leave the source's BOTTOM; loop-backs leave its TOP; the target is
      // always entered from ABOVE (top face). Anchors sit at the lane-side
      // extreme of the face (k1 ±1e6), outside the gap's corridor bundle,
      // inner lanes first (k2).
      const upward = layerOf(e.target) < layerOf(e.source);
      const k1 = r.side === 1 ? 1e6 : -1e6;
      const k2 = r.laneX * r.side;
      pushFace(e.source, upward ? "top" : "bottom", { edgeId: e.id, endpoint: "s", k1, k2, tie: e.id });
      pushFace(e.target, "top", { edgeId: e.id, endpoint: "t", k1, k2, tie: e.id });
    }
  }
  for (const e of selfLoops) {
    const s = box.get(e.source)!;
    // Self-loops sit between the upward and downward lane groups.
    pushFace(e.source, "right", { edgeId: e.id, endpoint: "s", k1: 0.5, k2: s.right + 44, tie: e.id });
  }

  const srcAnchor = new Map<string, { x: number; y: number }>();
  const tgtAnchor = new Map<string, { x: number; y: number }>();

  for (const [key, items] of faceMap) {
    const sep = key.lastIndexOf("|");
    const nodeId = key.slice(0, sep);
    const face = key.slice(sep + 1) as "bottom" | "top" | "left" | "right";
    const b = box.get(nodeId)!;
    const horizontal = face === "bottom" || face === "top";
    const w = b.right - b.left;
    const h = b.bottom - b.top;
    const half = horizontal ? Math.max(0, w / 2 - 20) : Math.max(0, h / 2 - 12);
    const maxStep = horizontal ? 28 : 16;

    items.sort((a, z) => a.k1 - z.k1 || a.k2 - z.k2 || (a.tie < z.tie ? -1 : 1));
    const n = items.length;
    const step = n > 1 ? Math.min((2 * half) / (n - 1), maxStep) : 0;
    const start = -step * (n - 1) / 2;
    items.forEach((item, i) => {
      const off = start + i * step;
      const anchor = horizontal
        ? { x: b.cx + off, y: face === "bottom" ? b.bottom : b.top }
        : { x: face === "right" ? b.right : b.left, y: b.cy + off };
      (item.endpoint === "s" ? srcAnchor : tgtAnchor).set(item.edgeId, anchor);
    });
  }

  // ── Geometry emission ──────────────────────────────────────────────────────

  const rectOf = (id: string): CelonisRect => {
    const b = box.get(id)!;
    return { x: b.left, y: b.top, w: b.right - b.left, h: b.bottom - b.top };
  };

  const STUB = 24; // vertical run before the first ortho corner

  for (const r of bezierRequests) {
    const e = r.edge;
    const terminal = isTerminal(e.source) || isTerminal(e.target);
    const a = srcAnchor.get(e.id)!;
    const z = tgtAnchor.get(e.id)!;

    if (exact) {
      // Live-routed Celonis edge: emit a routing PLAN; the renderer rebuilds
      // the path from current node rects every render (drag-proof).
      const f = opts.edgeFrequency(e.source, e.target);
      const src = box.get(e.source)!;
      const plan: CelonisRoutePlan = {
        kind: r.bow !== 0 ? "lane" : "chord",
        exitOffsetX: a.x - src.cx,
        ...(r.bow !== 0 ? { laneX: src.cx + r.bow } : {}),
        ...(r.kind === "twin"
          ? {
              twinBow: (a.x >= src.cx ? 1 : -1) * 14,
              labelT: 0.3,
              labelDX: r.downward ? -26 : 26,
            }
          : {}),
        arrowScale: arrowScale(f, exactMaxFreq),
      };
      const built = buildCelonisEdgePath(rectOf(e.source), rectOf(e.target), plan);
      geoByEdgeId.set(e.id, {
        kind: r.kind,
        points: [a, z],
        plan,
        labelPos: built.labelPoint,
        classic: true,
        expected: expectedOf(e),
      });
      continue;
    }

    let pts: { x: number; y: number }[];
    let bow: number | undefined;
    if (!ortho) {
      pts = [a, z];
      bow = r.bow !== 0 ? r.bow : undefined;
    } else if (r.bow !== 0) {
      // Long center-channel edge: vertical run through the (empty) center
      // channel at the bow offset, entering the target from above.
      const bx = box.get(e.source)!.cx + r.bow;
      pts = r.downward
        ? [a, { x: a.x, y: a.y + STUB }, { x: bx, y: a.y + STUB }, { x: bx, y: z.y - STUB }, { x: z.x, y: z.y - STUB }, z]
        : [a, { x: a.x, y: a.y - STUB }, { x: bx, y: a.y - STUB }, { x: bx, y: z.y + STUB }, { x: z.x, y: z.y + STUB }, z];
    } else if (Math.abs(a.x - z.x) < 0.5) {
      pts = [a, z]; // same column — straight vertical
    } else {
      // Celonis dogleg: down/up to the gap middle, across, into the target.
      const midY = (a.y + z.y) / 2;
      pts = [a, { x: a.x, y: midY }, { x: z.x, y: midY }, z];
    }

    geoByEdgeId.set(e.id, {
      kind: r.kind,
      points: pts,
      bow,
      dashed: terminal || undefined,
      classic: classicFlag,
      expected: expectedOf(e),
    });
  }

  for (const r of laneRequests) {
    const e = r.edge;
    const terminal = isTerminal(e.source) || isTerminal(e.target);
    const a = srcAnchor.get(e.id)!;
    const z = tgtAnchor.get(e.id)!;

    if (exact) {
      const f = opts.edgeFrequency(e.source, e.target);
      const src = box.get(e.source)!;
      const plan: CelonisRoutePlan = {
        kind: "lane",
        exitOffsetX: a.x - src.cx,
        laneX: r.laneX,
        arrowScale: arrowScale(f, exactMaxFreq),
      };
      const built = buildCelonisEdgePath(rectOf(e.source), rectOf(e.target), plan);
      geoByEdgeId.set(e.id, {
        kind: "lane",
        points: [a, { x: r.laneX, y: a.y }, { x: r.laneX, y: z.y }, z],
        plan,
        labelPos: built.labelPoint,
        classic: true,
        expected: expectedOf(e),
      });
      continue;
    }

    let pts: { x: number; y: number }[];
    let labelPos: { x: number; y: number };
    if (!ortho) {
      pts = [a, { x: r.laneX, y: a.y }, { x: r.laneX, y: z.y }, z];
      labelPos = { x: r.laneX, y: (a.y + z.y) / 2 };
    } else {
      // Stub out of the horizontal face, run the outside lane vertically,
      // re-enter the target from ABOVE — the Celonis loop-back shape.
      const sOut = a.y > box.get(e.source)!.cy ? a.y + STUB : a.y - STUB;
      const tIn = z.y - STUB;
      pts = [a, { x: a.x, y: sOut }, { x: r.laneX, y: sOut }, { x: r.laneX, y: tIn }, { x: z.x, y: tIn }, z];
      labelPos = { x: r.laneX, y: (sOut + tIn) / 2 };
    }

    geoByEdgeId.set(e.id, {
      kind: "lane",
      points: pts,
      labelPos,
      dashed: terminal || undefined,
      classic: classicFlag,
      expected: expectedOf(e),
    });
  }

  for (const e of selfLoops) {
    const s = box.get(e.source)!;
    const y0 = srcAnchor.get(e.id)?.y ?? s.cy;
    if (exact) {
      const plan: CelonisRoutePlan = {
        kind: "selfloop",
        exitOffsetX: y0 - s.cy,
        arrowScale: 0.5,
      };
      const built = buildCelonisEdgePath(rectOf(e.source), rectOf(e.source), plan);
      geoByEdgeId.set(e.id, {
        kind: "selfloop",
        points: [
          { x: s.right, y: y0 - 10 },
          { x: s.right + 44, y: y0 },
          { x: s.right, y: y0 + 10 },
        ],
        plan,
        labelPos: built.labelPoint,
        classic: true,
        expected: expectedOf(e),
      });
      continue;
    }
    geoByEdgeId.set(e.id, {
      kind: "selfloop",
      points: [
        { x: s.right, y: y0 - 10 },
        { x: s.right + 44, y: y0 },
        { x: s.right, y: y0 + 10 },
      ],
      labelPos: { x: s.right + 48, y: y0 },
      classic: classicFlag,
      expected: expectedOf(e),
    });
  }

  // ── Pass 6: label collision for the bezier kinds ───────────────────────────

  const bezierByLayer = new Map<number, { id: string; midX: number; midY: number; geo: CelonisEdgeGeometry }[]>();
  for (const e of edges) {
    const geo = geoByEdgeId.get(e.id);
    if (!geo || (geo.kind !== "forward" && geo.kind !== "terminal" && geo.kind !== "twin")) continue;
    const [p0, p1] = [geo.points[0]!, geo.points[geo.points.length - 1]!];
    const l = Math.min(layerOf(e.source), layerOf(e.target));
    (bezierByLayer.get(l) ?? bezierByLayer.set(l, []).get(l)!).push({
      id: e.id,
      midX: (p0.x + p1.x) / 2,
      midY: (p0.y + p1.y) / 2,
      geo,
    });
  }
  for (const group of bezierByLayer.values()) {
    group.sort((a, b) => a.midX - b.midX);
    group.forEach((item, i) => {
      if (item.geo.kind === "twin") {
        // Stagger the pair: each label sits 30% along ITS OWN path — since the
        // two paths run in opposite directions that lands them at 30% vs 70%
        // of the shared gap (0.4·gap apart vertically), plus a horizontal
        // nudge toward each edge's own side.
        const [p0, p1] = [item.geo.points[0]!, item.geo.points[1]!];
        const downward = p1.y > p0.y;
        const tt = 0.3;
        item.geo.labelPos = {
          x: p0.x + (p1.x - p0.x) * tt + (downward ? -26 : 26),
          y: p0.y + (p1.y - p0.y) * tt,
        };
      } else {
        item.geo.labelPos = { x: item.midX, y: item.midY + (i % 2 === 0 ? -9 : 9) };
      }
    });
  }

  // ── Commit ────────────────────────────────────────────────────────────────

  const outEdges = edges.map((e) => {
    const geo = geoByEdgeId.get(e.id);
    if (!geo) return e;
    return { ...e, data: { ...(e.data ?? {}), celonis: geo } } as unknown as Edge<TE>;
  });

  return { nodes: positioned, edges: outEdges };
}
