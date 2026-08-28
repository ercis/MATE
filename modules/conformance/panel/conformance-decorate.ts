/**
 * Conformance-overlay decoration for the BPMN canvas.
 *
 * Adapted from the discovery module's frequency heatmap. Instead of colouring
 * tasks by how *often* they run, it colours them by how much they *deviate*
 * from the reference model: green = conforming, a red ramp = increasing
 * deviation, amber-dashed = a model task whose label never appears in the log
 * (the #1 conformance gotcha - a naming mismatch, not a real deviation).
 *
 * `CONF_COLORS` / `devColor` are the single source of truth for these colours:
 * the injected canvas CSS *and* the panel legend both read them, so the legend
 * can never drift from what is actually painted. (They are plain inline values
 * on purpose - Tailwind only compiles classes it sees in `apps/web`, so utility
 * classes that exist only in module sources silently produce no CSS.)
 *
 * Label matching is EXACT after canonicalisation (trim + collapse whitespace +
 * case-fold), mirroring the backend. There is deliberately no fuzzy matching:
 * "Aproval" never matches "Approval".
 *
 * Everything is view-only: it decorates via diagram-js *markers* (CSS classes)
 * and *overlays* (floating HTML), never via `modeling.*`, so it never marks the
 * model dirty and is excluded from export.
 */

// bpmn-js' own default stroke/fill literals - see BPMN_THEME_COLORS.
import { black, white } from "bpmn-js/lib/draw/BpmnRenderUtil";

import type { PerActivityDeviation } from "./types";

export type BpmnModelerLike = { get<T = unknown>(name: string): T };

interface BElement {
  id: string;
  type: string;
  businessObject?: { name?: string; $type?: string };
}
interface ElementRegistry {
  getAll(): BElement[];
}
interface Overlays {
  add(elementId: string, type: string, opts: unknown): string;
  remove(filter: { type?: string; element?: string }): void;
}
interface Canvas {
  addMarker(id: string, cls: string): void;
  removeMarker(id: string, cls: string): void;
  scrollToElement(el: BElement | string): void;
}

/** Number of discrete red deviation buckets. Mirrored by the injected CSS. */
export const DEV_BUCKETS = 5;

/**
 * Exact colours painted on the canvas, echoed by the panel legend and tables.
 * Inline values, not Tailwind classes: module sources sit outside the host
 * app's Tailwind scan, so panel-only utility classes compile to no CSS at all.
 */
export const CONF_COLORS = {
  /** Task in the model that the log replays without deviations. */
  ok: { fill: "hsl(151 55% 93%)", stroke: "hsl(151 48% 40%)" },
  /** Model task whose label never occurs in the log (name mismatch / never executed). */
  unmatched: { fill: "hsl(38 92% 92%)", stroke: "hsl(33 92% 45%)" },
  /** Deviation-count badge pill. */
  badge: "hsl(0 72% 42%)",
  /** "not in log" warning badge pill. */
  badgeWarn: "hsl(33 90% 42%)",
  /** Red used by deviation bar charts / severity bars. */
  chart: "hsl(0 72% 51%)",
} as const;

/**
 * Canonical key for activity-label matching: trim, collapse internal
 * whitespace, case-fold. Matching stays EXACT on this key - whitespace/case
 * variants unify, but one wrong letter is a different activity ("Aproval"
 * never matches "Approval"). Mirrors the backend's `_canon_label`; there is
 * deliberately no fuzzy matching.
 */
export function canonLabel(s: string): string {
  return s.replace(/\s+/g, " ").trim().toLowerCase();
}

export interface DeviationInfo {
  deviations: number;
  logMoves: number;
  modelMoves: number;
  matched: boolean;
}

export interface DeviationMaps {
  byActivity: Map<string, DeviationInfo>;
  /** Fallback keyed by `canonLabel` - exact-after-normalisation, never fuzzy. */
  byCanonical: Map<string, DeviationInfo>;
  maxDeviation: number;
}

export function buildDeviationMaps(
  perActivity: PerActivityDeviation[] | undefined,
): DeviationMaps {
  const byActivity = new Map<string, DeviationInfo>();
  const byCanonical = new Map<string, DeviationInfo>();
  let maxDeviation = 0;
  for (const a of perActivity ?? []) {
    const info: DeviationInfo = {
      deviations: a.deviations,
      logMoves: a.log_moves,
      modelMoves: a.model_moves,
      matched: a.matched,
    };
    byActivity.set(a.activity, info);
    // First wins on canonical collisions (rows arrive sorted worst-first).
    const key = canonLabel(a.activity);
    if (!byCanonical.has(key)) byCanonical.set(key, info);
    if (a.deviations > maxDeviation) maxDeviation = a.deviations;
  }
  return { byActivity, byCanonical, maxDeviation };
}

/** Exact lookup first, then trim/case-normalised exact - never fuzzy. */
function lookupDeviation(maps: DeviationMaps, name: string): DeviationInfo | undefined {
  return maps.byActivity.get(name) ?? maps.byCanonical.get(canonLabel(name));
}

function isTask(el: BElement): boolean {
  return /Task$/.test(el.type) || el.type === "bpmn:SubProcess";
}
function nameOf(el: BElement | undefined): string | undefined {
  const n = el?.businessObject?.name;
  return n && n.length > 0 ? n : undefined;
}
function bucketOf(ratio: number): number {
  if (ratio <= 0) return 0;
  return Math.max(1, Math.min(DEV_BUCKETS, Math.ceil(ratio * DEV_BUCKETS)));
}

export interface ConformanceDecorOptions {
  maps: DeviationMaps;
  heatmap: boolean;
  labels: boolean;
  /** Alignments expose log/model moves separately; show them as `+L −M`. */
  alignments: boolean;
}

const DEV_CLASSES = Array.from({ length: DEV_BUCKETS }, (_, i) => `ff-conf-dev-${i + 1}`);
const STATE_CLASSES = ["ff-conf-ok", "ff-conf-unmatched"];

function clearDecoration(modeler: BpmnModelerLike): void {
  const registry = modeler.get<ElementRegistry>("elementRegistry");
  const overlays = modeler.get<Overlays>("overlays");
  const canvas = modeler.get<Canvas>("canvas");
  overlays.remove({ type: "ff-conf" });
  for (const el of registry.getAll()) {
    for (const c of DEV_CLASSES) canvas.removeMarker(el.id, c);
    for (const c of STATE_CLASSES) canvas.removeMarker(el.id, c);
  }
}

/** Re-apply the conformance overlay from scratch. Idempotent. */
export function applyConformanceOverlay(
  modeler: BpmnModelerLike,
  opts: ConformanceDecorOptions,
): void {
  const { maps, heatmap, labels, alignments } = opts;
  clearDecoration(modeler);

  const registry = modeler.get<ElementRegistry>("elementRegistry");
  const overlays = modeler.get<Overlays>("overlays");
  const canvas = modeler.get<Canvas>("canvas");

  for (const el of registry.getAll()) {
    if (!isTask(el)) continue;
    const name = nameOf(el);
    if (!name) continue;
    const info = lookupDeviation(maps, name);
    if (info === undefined) continue;

    if (!info.matched) {
      if (heatmap) canvas.addMarker(el.id, "ff-conf-unmatched");
      if (labels) {
        overlays.add(el.id, "ff-conf", {
          position: { top: -10, right: 12 },
          html: `<div class="ff-conf-badge ff-conf-badge-warn" title="In the model but never recorded in the log - likely an activity-name mismatch">not in log</div>`,
        });
      }
      continue;
    }

    if (info.deviations <= 0) {
      if (heatmap) canvas.addMarker(el.id, "ff-conf-ok");
      continue;
    }

    if (heatmap) {
      const ratio = maps.maxDeviation > 0 ? info.deviations / maps.maxDeviation : 0;
      const b = bucketOf(ratio);
      if (b > 0) canvas.addMarker(el.id, `ff-conf-dev-${b}`);
    }
    if (labels) {
      const text = alignments
        ? `+${info.logMoves} −${info.modelMoves}`
        : `${info.deviations}`;
      const title = alignments
        ? `${info.logMoves} in the log but not allowed by the model (+) · ${info.modelMoves} required by the model but skipped in the log (−)`
        : `${info.deviations} deviation(s): the recorded process differs from the model at this step`;
      overlays.add(el.id, "ff-conf", {
        position: { top: -10, right: 12 },
        html: `<div class="ff-conf-badge" title="${title}">${text}</div>`,
      });
    }
  }
}

/** Centre an activity by (case-insensitive substring) name, with a transient
 *  highlight. Returns false when nothing matches. */
export function locateActivity(modeler: BpmnModelerLike, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return false;
  const registry = modeler.get<ElementRegistry>("elementRegistry");
  const canvas = modeler.get<Canvas>("canvas");

  let hit: BElement | undefined;
  for (const el of registry.getAll()) {
    if (!isTask(el)) continue;
    const name = nameOf(el)?.toLowerCase();
    if (name && name.includes(q)) {
      hit = el;
      break;
    }
  }
  if (!hit) return false;

  canvas.scrollToElement(hit);
  canvas.addMarker(hit.id, "ff-conf-hit");
  const id = hit.id;
  window.setTimeout(() => {
    try {
      canvas.removeMarker(id, "ff-conf-hit");
    } catch {
      /* canvas torn down before the timeout fired - ignore. */
    }
  }, 2200);
  return true;
}

// diagram-js-minimap ships a top-right, light-only panel with an orange
// viewport box. Restyle it to match the React Flow minimap: bottom-right at
// 15px, 200x150 card surface, rounded, neutral viewport stroke (themed for
// dark to mirror the RF mask). The canvas fades it via the `ff-minimap-hidden`
// class (opacity gate); we drive open/close ourselves, so the toggle is hidden.
const MINIMAP_CSS = `
.djs-minimap{top:auto!important;bottom:15px!important;right:15px!important;background:var(--card)!important;border:1px solid var(--border)!important;border-radius:6px!important;box-shadow:0 1px 2px rgba(0,0,0,.08)!important;transition:opacity .3s ease!important;}
.djs-minimap .map{width:200px;height:150px;}
.djs-minimap .toggle{display:none!important;}
.djs-minimap .viewport-dom{border:2px solid rgba(0,0,0,.4)!important;border-radius:3px!important;}
.dark .djs-minimap .viewport-dom{border-color:rgba(255,255,255,.5)!important;}
.djs-minimap.ff-minimap-hidden{opacity:0!important;pointer-events:none!important;}
`;

/**
 * `bpmnRenderer` config making the diagram follow the app theme. Pass it to
 * every `NavigatedViewer` that renders through this decorator.
 *
 * bpmn-js resolves each element's fill/stroke/label colour once, at import
 * time, and tiny-svg writes the result into that node's **inline style**
 * (`node.style.stroke = ...` - these are not presentation attributes, so no
 * ordinary stylesheet can re-theme them). Its built-in defaults are `black` on
 * `white`, which on a dark canvas paints near-black lines on a near-black
 * background.
 *
 * So we hand bpmn-js CSS *variables* instead of colours: it writes
 * `stroke: var(--ff-bpmn-stroke)` into the inline style, and `themeCss()`
 * re-points that variable per theme. The canvas then re-themes live on a theme
 * toggle - no viewer rebuild, no second `layoutProcess`, no lost pan/zoom.
 *
 * These are only *defaults*: `getFillColor`/`getStrokeColor` read the diagram's
 * own BPMN DI (`color:background-color` / `bioc:fill`) first, so theming can
 * never override a colour a model declares for itself. That would matter here,
 * where the reference model is a user-uploaded file - except `ensureLayout`
 * regenerates DI through bpmn-auto-layout, which drops an upload's `bioc:*`
 * long before the renderer sees it. Uploaded colours are already lost today;
 * theming is not what loses them.
 */
export const BPMN_THEME_COLORS = {
  defaultFillColor: "var(--ff-bpmn-fill)",
  defaultStrokeColor: "var(--ff-bpmn-stroke)",
  defaultLabelColor: "var(--ff-bpmn-label)",
} as const;

/**
 * Resolve the variables behind `BPMN_THEME_COLORS`. Light mode restates
 * bpmn-js' own `black`/`white`, so it renders exactly as it did before any of
 * this existed; dark mode swaps in the app's surface/foreground tokens.
 *
 * @param litShapeClasses Decoration markers that paint a *light* fill in both
 *   themes (the deviation ramp plus the ok/unmatched states - their lightness
 *   range is tuned to keep dark labels readable). Their labels must therefore
 *   stay dark, so they re-assert bpmn-js' default label colour over the themed
 *   inline style.
 */
function themeCss(litShapeClasses: string[]): string {
  const litLabels = litShapeClasses
    .map((c) => `.dark .djs-element.${c} .djs-visual text`)
    .join(",");
  return `
.djs-container{--ff-bpmn-fill:${white};--ff-bpmn-stroke:${black};--ff-bpmn-label:${black};}
.dark .djs-container{--ff-bpmn-fill:var(--card);--ff-bpmn-stroke:var(--foreground);--ff-bpmn-label:var(--foreground);}
${litLabels}{fill:${black}!important;}
`;
}

const STYLE_ID = "ff-conformance-styles";

/** Map a 0..1 deviation ratio to a red fill/stroke pair. Exported so the
 *  legend can render exactly the same ramp the canvas paints. */
export function devColor(ratio: number): { fill: string; stroke: string } {
  const r = Math.max(0, Math.min(1, ratio));
  const sat = 70 + r * 25; // 70% → 95%
  const fillL = 95 - r * 30; // 95% → 65% (keep dark labels readable)
  const strokeL = 55 - r * 25; // 55% → 30%
  return { fill: `hsl(0 ${sat}% ${fillL}%)`, stroke: `hsl(0 ${sat}% ${strokeL}%)` };
}

/** Idempotently inject the overlay stylesheet (the module bundler has no CSS
 *  loader, so we ship CSS as a <style> tag rather than an import). */
export function injectConformanceStyles(): void {
  if (typeof document === "undefined" || document.getElementById(STYLE_ID)) return;

  const devRules: string[] = [];
  for (let i = 1; i <= DEV_BUCKETS; i++) {
    const { fill, stroke } = devColor(i / DEV_BUCKETS);
    devRules.push(
      `.djs-element.ff-conf-dev-${i} .djs-visual > :nth-child(1){fill:${fill}!important;stroke:${stroke}!important;}`,
    );
  }

  const css = `
.djs-element.ff-conf-ok .djs-visual > :nth-child(1){fill:${CONF_COLORS.ok.fill}!important;stroke:${CONF_COLORS.ok.stroke}!important;}
.djs-element.ff-conf-unmatched .djs-visual > :nth-child(1){fill:${CONF_COLORS.unmatched.fill}!important;stroke:${CONF_COLORS.unmatched.stroke}!important;stroke-dasharray:4 3!important;}
.djs-element.ff-conf-hit .djs-outline{stroke:#2563eb!important;stroke-width:3px!important;stroke-dasharray:none!important;display:block!important;}
${devRules.join("\n")}
.ff-conf-badge{font:600 10px/1.35 ui-sans-serif,system-ui,sans-serif;background:${CONF_COLORS.badge};color:#fff;padding:1px 5px;border-radius:6px;white-space:nowrap;pointer-events:none;box-shadow:0 1px 2px rgba(0,0,0,.25);}
.ff-conf-badge-warn{background:${CONF_COLORS.badgeWarn};}
${MINIMAP_CSS}
${themeCss([...DEV_CLASSES, ...STATE_CLASSES])}
`;
  const style = document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = css;
  document.head.appendChild(style);
}
