"use client";

import { create } from "zustand";

// --------------------------------------------------------------------------
// Settings shapes
// --------------------------------------------------------------------------

export type LayoutDirection = "LR" | "TB" | "RL" | "BT";
export type EdgeRouting = "orthogonal" | "spline" | "straight";
export type FrequencyDisplayMode = "count" | "ratio" | "per-case";
export type Theme = "default" | "monochrome" | "colorblind";

export interface GeneralSettings {
  layoutDirection: LayoutDirection;
  edgeRouting: EdgeRouting;
  showMinimap: boolean;
  showGrid: boolean;
  nodeLabelMaxLength: number;
  frequencyDisplayMode: FrequencyDisplayMode;
  colorIntensity: number; // 0..1
  theme: Theme;
}

export interface DfgRenderSettings {
  /** Fraction of activities to show, sorted by frequency. 1 = all, 0.5 = top
   *  half. "auto" = knee of the cumulative frequency-coverage curve (Kneedle),
   *  resolved client-side from the DFG payload (modules/discovery/panel/
   *  auto-threshold.ts); dragging the slider writes a number and freezes it. */
  activitiesShown: number | "auto";
  /** Fraction of edges to show, sorted by frequency, after the activity
   *  filter. "auto" = knee of the FULL edge frequency curve applied as a
   *  frequency cutoff to the candidates. */
  connectionsShown: number | "auto";
  hideSelfLoops: boolean;
  /** Keep only the top N% of visible edges by count (100 = show all). Unlike
   *  the connections slider this does NOT affect node visibility – nodes stay
   *  shown even if all their edges are removed. */
  edgeTopPercent: 100 | 95 | 90 | 85 | 80 | 70;
  edgeLabel: "count" | "duration" | "off";
  edgeThicknessEncoding: "linear" | "log" | "off";
  /** DFG layout algorithm. "celonis-classic" = the measured client-side
   *  Celonis clone; "backbone" (optimized backbone IP), "backbone-v2" (same
   *  placement plus an obstacle-aware edge router) and "sugiyama" (layered
   *  baseline) are computed server-side by the discovery module
   *  (POST /dfg/layout). Unknown persisted values coerce in `selectDfg`. */
  layoutMode: "celonis-classic" | "backbone" | "backbone-v2" | "sugiyama";
}

export interface PetriRenderSettings {
  showInvisibleTransitions: boolean;
  transitionLabelMode: "activity" | "id" | "both";
  placeMode: "rings" | "count";
  highlightMarkings: boolean;
  showArcWeights: boolean;
  /** Reading direction of the net. Petri-scoped rather than reusing
   *  `general.layoutDirection` so it can default to LR – the standard for
   *  place/transition nets – without flipping the Heuristics net too. */
  layoutDirection: LayoutDirection;
  /** `elk.layered.mergeEdges`: arcs leaving one place share a trunk and branch
   *  late (BPMN-gateway look) instead of each getting its own exit point. */
  mergeArcs: boolean;
}

export interface ProcessTreeRenderSettings {
  orientation: "vertical" | "horizontal";
  operatorStyle: "icon" | "text" | "abbrev";
  maxDepth: number | null;
  foldTauLeaves: boolean;
}

export interface HeuristicsRenderSettings {
  edgeLabel: "dependency" | "count" | "both";
  hideRareArcs: boolean;
  /** Per-(log, module) threshold sliders. Held client-side because the
   *  cascade of /config PUT + refetchType:"all" was crashing inactive
   *  ILP/process-tree queries (OOM / recursion) mid-drag. */
  dependencyThreshold: number;
  andThreshold: number;
  loopTwoThreshold: number;
}

/**
 * Settings are stored per `(logId, scope)`.
 *
 * `scope` used to be the module id, which meant every view of a module on a
 * log shared one set of render settings. That is right for a panel and wrong
 * for a dashboard card: two cards of the same widget could never differ, and
 * neither could differ from the panel — a discovery process-map card silently
 * inherited whatever the user last set in the panel, with no way to change it.
 *
 * A panel's scope is still its module id (`panelScope`), so every blob
 * persisted before this change keeps resolving byte-identically — no store
 * migration. A card gets `card:<placement id>` instead.
 *
 * Card scopes must NOT reach the per-user preference blob: they belong to the
 * board (so they travel with a share or an export) and keying a user-wide blob
 * by card id would grow it without bound. `withoutCardScopes` strips them.
 */
export type VizKey = "dfg" | "petri" | "process_tree" | "heuristics" | "prefix_tree";

/** Scope for a module panel — shared across that module's views on a log. */
export function panelScope(moduleId: string): string {
  return moduleId;
}

/** Scope for one placed dashboard card. */
export function cardScope(cardId: string): string {
  return `card:${cardId}`;
}

export function isCardScope(scope: string): boolean {
  return scope.startsWith("card:");
}

/**
 * Drop every card scope from a settings blob.
 *
 * Applied on the way OUT to the per-user preference sync: card settings live
 * in the board's own config instead, so they survive a share/export and don't
 * accumulate in a user-wide document keyed by dead card ids.
 */
export function withoutCardScopes(data: Record<string, unknown>): Record<string, unknown> {
  const strip = <T,>(byLog: Record<string, Record<string, T>> | undefined) => {
    if (!byLog) return byLog;
    const out: Record<string, Record<string, T>> = {};
    for (const [logId, byScope] of Object.entries(byLog)) {
      const kept = Object.fromEntries(
        Object.entries(byScope).filter(([scope]) => !isCardScope(scope)),
      );
      if (Object.keys(kept).length > 0) out[logId] = kept;
    }
    return out;
  };
  return {
    ...data,
    perLog: strip(data.perLog as VizSettingsState["perLog"] | undefined),
    positions: strip(data.positions as VizSettingsState["positions"] | undefined),
  };
}

export interface PerVizSettings {
  dfg?: DfgRenderSettings;
  petri?: PetriRenderSettings;
  process_tree?: ProcessTreeRenderSettings;
  heuristics?: HeuristicsRenderSettings;
}

export type NodePositions = Record<string, { x: number; y: number }>;

interface VizSettingsState {
  general: GeneralSettings;
  // perLog[logId][scope][vizKey] = render settings
  perLog: Record<string, Record<string, PerVizSettings>>;
  // positions[logId][scope][vizKey] = { nodeId: {x,y} }
  positions: Record<
    string,
    Record<string, Partial<Record<VizKey, NodePositions>>>
  >;

  setGeneral: (patch: Partial<GeneralSettings>) => void;
  resetGeneral: () => void;

  setDfg: (logId: string, scope: string, patch: Partial<DfgRenderSettings>) => void;
  setPetri: (logId: string, scope: string, patch: Partial<PetriRenderSettings>) => void;
  setProcessTree: (logId: string, scope: string, patch: Partial<ProcessTreeRenderSettings>) => void;
  setHeuristics: (logId: string, scope: string, patch: Partial<HeuristicsRenderSettings>) => void;

  resetForLog: (logId: string, scope?: string) => void;

  setNodePosition: (logId: string, scope: string, viz: VizKey, nodeId: string, pos: { x: number; y: number }) => void;
  setNodePositions: (logId: string, scope: string, viz: VizKey, patch: NodePositions) => void;
  resetPositions: (logId: string, scope: string, viz?: VizKey) => void;

  // Replace the data slice with a server blob merged over defaults. Used by
  // the per-user server-state sync (see `lib/server-persist.ts`).
  hydrate: (data: Record<string, unknown>) => void;
}

// --------------------------------------------------------------------------
// Defaults
// --------------------------------------------------------------------------

export const DEFAULT_GENERAL: GeneralSettings = {
  // Top-to-bottom by default – matches Celonis's DFG and is what most process
  // mining tooling defaults to. Users can switch to LR via Settings → General.
  layoutDirection: "TB",
  edgeRouting: "orthogonal",
  showMinimap: true,
  showGrid: true,
  nodeLabelMaxLength: 32,
  frequencyDisplayMode: "count",
  colorIntensity: 0.6,
  theme: "default",
};

export const DEFAULT_DFG: DfgRenderSettings = {
  // Auto-simplified on first open (Celonis-style): the knee of the frequency
  // curve decides how many activities/connections show; users can drag the
  // sliders up to 100% at any time. Persisted numeric values from before the
  // "auto" sentinel keep working unchanged.
  activitiesShown: "auto",
  connectionsShown: "auto",
  hideSelfLoops: false,
  edgeTopPercent: 100,
  edgeLabel: "count",
  edgeThicknessEncoding: "log",
  // The DFG ships exactly one layout: the measured Celonis clone.
  layoutMode: "celonis-classic",
};

export const DEFAULT_PETRI: PetriRenderSettings = {
  showInvisibleTransitions: true,
  transitionLabelMode: "activity",
  placeMode: "rings",
  highlightMarkings: true,
  showArcWeights: true,
  layoutDirection: "LR",
  mergeArcs: false,
};

export const DEFAULT_PROCESS_TREE: ProcessTreeRenderSettings = {
  orientation: "vertical",
  operatorStyle: "icon",
  maxDepth: null,
  foldTauLeaves: false,
};

export const DEFAULT_HEURISTICS: HeuristicsRenderSettings = {
  edgeLabel: "both",
  hideRareArcs: false,
  dependencyThreshold: 0.5,
  andThreshold: 0.65,
  loopTwoThreshold: 0.5,
};

// --------------------------------------------------------------------------
// Helpers – set into nested per-(log, module) record without losing siblings.
// --------------------------------------------------------------------------

function patchPerViz<K extends keyof PerVizSettings>(
  perLog: VizSettingsState["perLog"],
  logId: string,
  scope: string,
  key: K,
  defaults: NonNullable<PerVizSettings[K]>,
  patch: Partial<NonNullable<PerVizSettings[K]>>,
): VizSettingsState["perLog"] {
  const log = perLog[logId] ?? {};
  const mod = log[scope] ?? {};
  const current = (mod[key] ?? defaults) as NonNullable<PerVizSettings[K]>;
  const next = { ...current, ...patch } as PerVizSettings[K];
  return {
    ...perLog,
    [logId]: { ...log, [scope]: { ...mod, [key]: next } },
  };
}

function patchPositions(
  positions: VizSettingsState["positions"],
  logId: string,
  scope: string,
  viz: VizKey,
  patch: NodePositions,
): VizSettingsState["positions"] {
  const log = positions[logId] ?? {};
  const mod = log[scope] ?? {};
  const current = mod[viz] ?? {};
  return {
    ...positions,
    [logId]: {
      ...log,
      [scope]: { ...mod, [viz]: { ...current, ...patch } },
    },
  };
}

// --------------------------------------------------------------------------
// Store
// --------------------------------------------------------------------------

export const useVizSettings = create<VizSettingsState>()((set) => ({
      general: { ...DEFAULT_GENERAL },
      perLog: {},
      positions: {},

      setGeneral: (patch) => set((s) => ({ general: { ...s.general, ...patch } })),
      resetGeneral: () => set({ general: { ...DEFAULT_GENERAL } }),

      setDfg: (logId, scope, patch) =>
        set((s) => ({ perLog: patchPerViz(s.perLog, logId, scope, "dfg", DEFAULT_DFG, patch) })),
      setPetri: (logId, scope, patch) =>
        set((s) => ({ perLog: patchPerViz(s.perLog, logId, scope, "petri", DEFAULT_PETRI, patch) })),
      setProcessTree: (logId, scope, patch) =>
        set((s) => ({
          perLog: patchPerViz(s.perLog, logId, scope, "process_tree", DEFAULT_PROCESS_TREE, patch),
        })),
      setHeuristics: (logId, scope, patch) =>
        set((s) => ({ perLog: patchPerViz(s.perLog, logId, scope, "heuristics", DEFAULT_HEURISTICS, patch) })),

      resetForLog: (logId, scope) =>
        set((s) => {
          const log = s.perLog[logId];
          if (!log) return {};
          if (!scope) {
            const next = { ...s.perLog };
            delete next[logId];
            return { perLog: next };
          }
          const nextLog = { ...log };
          delete nextLog[scope];
          return { perLog: { ...s.perLog, [logId]: nextLog } };
        }),

      setNodePosition: (logId, scope, viz, nodeId, pos) =>
        set((s) => ({ positions: patchPositions(s.positions, logId, scope, viz, { [nodeId]: pos }) })),
      setNodePositions: (logId, scope, viz, patch) =>
        set((s) => ({ positions: patchPositions(s.positions, logId, scope, viz, patch) })),
      resetPositions: (logId, scope, viz) =>
        set((s) => {
          const log = s.positions[logId];
          if (!log) return {};
          const mod = log[scope];
          if (!mod) return {};
          if (!viz) {
            const nextLog = { ...log };
            delete nextLog[scope];
            return { positions: { ...s.positions, [logId]: nextLog } };
          }
          const nextMod = { ...mod };
          delete nextMod[viz];
          return { positions: { ...s.positions, [logId]: { ...log, [scope]: nextMod } } };
        }),

      // Server blob merged over defaults. `general` is deep-merged with
      // DEFAULT_GENERAL so a field added after the user last saved doesn't
      // arrive undefined and crash a consumer; perLog/positions reset to empty
      // when the server has none (also clears a prior account on switch).
      hydrate: (data) =>
        set({
          general: { ...DEFAULT_GENERAL, ...((data.general as Partial<GeneralSettings>) ?? {}) },
          perLog: (data.perLog as VizSettingsState["perLog"]) ?? {},
          positions: (data.positions as VizSettingsState["positions"]) ?? {},
        }),
}));

// --------------------------------------------------------------------------
// Convenience selectors
// --------------------------------------------------------------------------

// Coerced-settings cache: selectors MUST return stable references (see the
// EMPTY_POSITIONS note below) — building a fresh object per call makes
// useSyncExternalStore loop forever ("Maximum update depth exceeded").
const coercedDfg = new WeakMap<DfgRenderSettings, DfgRenderSettings>();

const VALID_DFG_LAYOUT_MODES: ReadonlySet<string> = new Set([
  "celonis-classic",
  "backbone",
  "backbone-v2",
  "sugiyama",
]);

export function selectDfg(state: VizSettingsState, logId: string, scope: string): DfgRenderSettings {
  const stored = state.perLog[logId]?.[scope]?.dfg;
  if (!stored) return DEFAULT_DFG;
  // Migration: older builds persisted since-removed layout modes — those
  // coerce to the Celonis clone. Cached per stored ref for stability.
  if (VALID_DFG_LAYOUT_MODES.has(stored.layoutMode as string)) return stored;
  let c = coercedDfg.get(stored);
  if (!c) {
    c = { ...stored, layoutMode: "celonis-classic" };
    coercedDfg.set(stored, c);
  }
  return c;
}

const coercedPetri = new WeakMap<PetriRenderSettings, PetriRenderSettings>();

const VALID_LAYOUT_DIRECTIONS: ReadonlySet<string> = new Set(["LR", "TB", "RL", "BT"]);

export function selectPetri(state: VizSettingsState, logId: string, scope: string): PetriRenderSettings {
  const stored = state.perLog[logId]?.[scope]?.petri;
  if (!stored) return DEFAULT_PETRI;
  // Migration: blobs persisted before `layoutDirection`/`mergeArcs` existed have
  // them `undefined`. That is not a soft default downstream – the direction is
  // fed straight into a `switch` that returns handle positions, so an undefined
  // value destructures to a crash. Fill both from the defaults, cached per
  // stored ref for reference stability (see the note above `coercedDfg`).
  if (VALID_LAYOUT_DIRECTIONS.has(stored.layoutDirection as string) && typeof stored.mergeArcs === "boolean") {
    return stored;
  }
  let c = coercedPetri.get(stored);
  if (!c) {
    c = {
      ...stored,
      layoutDirection: VALID_LAYOUT_DIRECTIONS.has(stored.layoutDirection as string)
        ? stored.layoutDirection
        : DEFAULT_PETRI.layoutDirection,
      mergeArcs: typeof stored.mergeArcs === "boolean" ? stored.mergeArcs : DEFAULT_PETRI.mergeArcs,
    };
    coercedPetri.set(stored, c);
  }
  return c;
}

export function selectProcessTree(state: VizSettingsState, logId: string, scope: string): ProcessTreeRenderSettings {
  return state.perLog[logId]?.[scope]?.process_tree ?? DEFAULT_PROCESS_TREE;
}

export function selectHeuristics(state: VizSettingsState, logId: string, scope: string): HeuristicsRenderSettings {
  return state.perLog[logId]?.[scope]?.heuristics ?? DEFAULT_HEURISTICS;
}

// Stable reference for the "no persisted positions yet" case. Returning a
// fresh `{}` from the selector would change identity on every call and put
// Zustand subscribers into an infinite re-render loop.
const EMPTY_POSITIONS: NodePositions = Object.freeze({}) as NodePositions;

export function selectNodePositions(
  state: VizSettingsState,
  logId: string,
  scope: string,
  viz: VizKey,
): NodePositions {
  return state.positions[logId]?.[scope]?.[viz] ?? EMPTY_POSITIONS;
}
