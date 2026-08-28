/**
 * Comparison-overlay decoration for the BPMN canvas.
 *
 * Ported from the conformance module's BPMN decorator (modules can't import each
 * other). Instead of colouring tasks by deviation, it colours them by how they
 * change from baseline (A) to comparison (B): green = added or grew, red = removed
 * or shrank, neutral = unchanged. The same per-activity map drives both diagrams –
 * a baseline-only task only exists on the A diagram (shown red, "gone"), a
 * comparison-only task only on the B diagram (green, "new").
 *
 * View-only: decorates via diagram-js *markers* (CSS classes) and *overlays*
 * (floating HTML), never via `modeling.*`, so it never marks the model dirty.
 */

// bpmn-js' own default stroke/fill literals - see BPMN_THEME_COLORS.
import { black, white } from "bpmn-js/lib/draw/BpmnRenderUtil";

import type { DfgDiffNode, DiffStatus } from "./types";

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

export type DeltaKind = "up" | "down" | "same";

export interface ComparisonInfo {
  status: DiffStatus;
  freqA: number;
  freqB: number;
  delta: number;
  kind: DeltaKind;
}

export type ActivityMap = Map<string, ComparisonInfo>;

/** Index the diff's per-activity rows by label, tagging each with its delta kind. */
export function buildActivityMap(activities: DfgDiffNode[]): ActivityMap {
  const map: ActivityMap = new Map();
  for (const a of activities) {
    const delta = a.freq_b - a.freq_a;
    const kind: DeltaKind =
      a.status === "only_b" || (a.status === "shared" && delta > 0)
        ? "up"
        : a.status === "only_a" || (a.status === "shared" && delta < 0)
          ? "down"
          : "same";
    map.set(a.label, { status: a.status, freqA: a.freq_a, freqB: a.freq_b, delta, kind });
  }
  return map;
}

const KIND_CLASSES = ["ff-cmp-up", "ff-cmp-down"];

function isTask(el: BElement): boolean {
  return /Task$/.test(el.type) || el.type === "bpmn:SubProcess";
}
function nameOf(el: BElement | undefined): string | undefined {
  const n = el?.businessObject?.name;
  return n && n.length > 0 ? n : undefined;
}

export interface ComparisonDecorOptions {
  map: ActivityMap;
  heatmap: boolean;
  labels: boolean;
}

function clearDecoration(modeler: BpmnModelerLike): void {
  const registry = modeler.get<ElementRegistry>("elementRegistry");
  const overlays = modeler.get<Overlays>("overlays");
  const canvas = modeler.get<Canvas>("canvas");
  overlays.remove({ type: "ff-cmp" });
  for (const el of registry.getAll()) {
    for (const c of KIND_CLASSES) canvas.removeMarker(el.id, c);
  }
}

/** Re-apply the comparison overlay from scratch. Idempotent. */
export function applyComparisonOverlay(
  modeler: BpmnModelerLike,
  opts: ComparisonDecorOptions,
): void {
  const { map, heatmap, labels } = opts;
  clearDecoration(modeler);

  const registry = modeler.get<ElementRegistry>("elementRegistry");
  const overlays = modeler.get<Overlays>("overlays");
  const canvas = modeler.get<Canvas>("canvas");

  for (const el of registry.getAll()) {
    if (!isTask(el)) continue;
    const name = nameOf(el);
    if (!name) continue;
    const info = map.get(name);
    if (!info) continue;

    if (heatmap && info.kind !== "same") canvas.addMarker(el.id, `ff-cmp-${info.kind}`);

    if (labels) {
      const text =
        info.status === "only_b"
          ? "new"
          : info.status === "only_a"
            ? "gone"
            : info.delta === 0
              ? ""
              : `Δ${info.delta > 0 ? "+" : ""}${info.delta}`;
      if (text) {
        const cls = info.kind === "down" ? "ff-cmp-badge ff-cmp-badge-down" : "ff-cmp-badge";
        overlays.add(el.id, "ff-cmp", {
          position: { top: -10, right: 12 },
          html: `<div class="${cls}" title="${name}: ${info.freqA} → ${info.freqB}">${text}</div>`,
        });
      }
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
  canvas.addMarker(hit.id, "ff-cmp-hit");
  const id = hit.id;
  window.setTimeout(() => {
    try {
      canvas.removeMarker(id, "ff-cmp-hit");
    } catch {
      /* canvas torn down before the timeout fired - ignore. */
    }
  }, 2200);
  return true;
}

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
 * never override a colour a model declares for itself. (Moot for the models we
 * render anyway - `ensureLayout` regenerates DI through bpmn-auto-layout, which
 * drops `bioc:*` long before the renderer sees it.)
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
 *   themes (the added/removed tints - their lightness range is tuned to keep
 *   dark labels readable). Their labels must therefore stay dark, so they
 *   re-assert bpmn-js' default label colour over the themed inline style.
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

const STYLE_ID = "ff-comparison-styles";

/** Idempotently inject the overlay stylesheet (the module bundler has no CSS
 *  loader, so we ship CSS as a <style> tag rather than an import). */
export function injectComparisonStyles(): void {
  if (typeof document === "undefined" || document.getElementById(STYLE_ID)) return;

  const css = `
.djs-element.ff-cmp-up .djs-visual > :nth-child(1){fill:hsl(151 55% 92%)!important;stroke:hsl(151 60% 35%)!important;}
.djs-element.ff-cmp-down .djs-visual > :nth-child(1){fill:hsl(0 75% 94%)!important;stroke:hsl(0 70% 45%)!important;}
.djs-element.ff-cmp-hit .djs-outline{stroke:#2563eb!important;stroke-width:3px!important;stroke-dasharray:none!important;display:block!important;}
.ff-cmp-badge{font:600 10px/1.35 ui-sans-serif,system-ui,sans-serif;background:hsl(151 60% 35%);color:#fff;padding:1px 5px;border-radius:6px;white-space:nowrap;pointer-events:none;box-shadow:0 1px 2px rgba(0,0,0,.25);}
.ff-cmp-badge-down{background:hsl(0 70% 45%);}
/* diagram-js-minimap: pin bottom-right at 15px (toolbar owns top-right), match
   the RF minimap's 200x150 card surface, neutralise the default orange viewport
   box (themed for dark), and fade via the ff-minimap-hidden opacity gate. */
.djs-minimap{top:auto!important;bottom:15px!important;right:15px!important;background:var(--card)!important;border:1px solid var(--border)!important;border-radius:6px!important;box-shadow:0 1px 2px rgba(0,0,0,.08)!important;transition:opacity .3s ease!important;}
.djs-minimap .map{width:200px;height:150px;}
.djs-minimap .toggle{display:none!important;}
.djs-minimap .viewport-dom{border:2px solid rgba(0,0,0,.4)!important;border-radius:3px!important;}
.dark .djs-minimap .viewport-dom{border-color:rgba(255,255,255,.5)!important;}
.djs-minimap.ff-minimap-hidden{opacity:0!important;pointer-events:none!important;}
${themeCss(KIND_CLASSES)}
`;
  const style = document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = css;
  document.head.appendChild(style);
}
