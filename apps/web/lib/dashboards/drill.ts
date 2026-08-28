"use client";

import { useSearchParams } from "next/navigation";

import type { WidgetDrill } from "@/lib/dashboard-queries";

/**
 * Drill-down: getting from a number on a dashboard card to the page that
 * explains it.
 *
 * A card used to be a dead end — you could see that "Approve" was the slowest
 * activity and had no way to act on it. Now a widget calls `onDrill(...)` from
 * a clicked mark and the platform resolves where that goes, while the card
 * header offers the same target as a plain link.
 *
 * Both go through `resolveDrillHref`, so the header button and an in-card click
 * can never disagree about the destination.
 */

/** Where a click should land. Every field is optional: `onDrill()` with no
 * argument means "open this card's module for the current log". */
export interface DrillTarget {
  /** Defaults to the card's own module. */
  moduleId?: string;
  /** Defaults to the board's bound log. */
  logId?: string;
  /** Query params. `undefined`/empty values are dropped, so a caller can pass
   * a possibly-absent value without guarding. */
  params?: Record<string, string | number | boolean | undefined | null>;
  hash?: string;
}

/** What the platform hands a widget. Undefined outside a dashboard (e.g. when
 * a panel embeds the widget directly), so always call it optionally. */
export type DrillHandler = (target?: DrillTarget) => void;

/**
 * The standard drill parameters.
 *
 * Shared vocabulary matters here: `discovery` links into `performance` with
 * `?activity=`, and that only works because both agree on the name. Prefer
 * these over inventing one; add to this list rather than to a single module.
 *
 * Note `from`/`to` are graph edge endpoints, NOT a time window — the time
 * window is `ts_from`/`ts_to`, kept deliberately distinct.
 */
export const DRILL_PARAMS = {
  /** Activity name. The one param that predates this contract. */
  activity: "activity",
  /** Directly-follows edge endpoints. */
  from: "from",
  to: "to",
  case: "case",
  variant: "variant",
  /** OCEL object type. */
  objectType: "object_type",
  /** ISO time window. */
  tsFrom: "ts_from",
  tsTo: "ts_to",
  /** Which panel tab/view to open on arrival. */
  view: "view",
  /** Which metric to focus. */
  metric: "metric",
} as const;

/** The module page a drill lands on. */
export function modulePath(logId: string, moduleId: string): string {
  return `/processes/${encodeURIComponent(logId)}/modules/${encodeURIComponent(moduleId)}`;
}

/**
 * The canonical variant detail page. Takes the RAW `variant_id` (the platform's
 * blake2b sequence hash) and encodes it once - never pre-encode.
 */
export function variantHref(logId: string, variantId: string): string {
  return `/processes/${encodeURIComponent(logId)}/variants/${encodeURIComponent(variantId)}`;
}

/**
 * The canonical activity detail page. Takes the RAW activity name and encodes
 * it once - never pre-encode. The name travels as a query param (not a path
 * segment) because names are arbitrary strings and proxies may normalize
 * percent-escapes inside path segments.
 */
export function activityHref(logId: string, activity: string): string {
  return `/processes/${encodeURIComponent(logId)}/activities?name=${encodeURIComponent(activity)}`;
}

/**
 * Resolve a drill into a URL.
 *
 * Returns `null` when there is nowhere to go — no bound log, or the manifest
 * disabled drilling. Callers render a disabled affordance rather than a link
 * that goes nowhere.
 *
 * Precedence: a param passed at click time beats the manifest's static params,
 * so a widget can override a default it declared. When no explicit module was
 * requested (neither click target nor manifest), a `variant`/`activity` param
 * lands on the platform's canonical entity view instead of the card's module —
 * `variant` beats `activity`, remaining params are dropped (entity pages read
 * canonical data). An explicit `moduleId` always keeps module routing.
 */
export function resolveDrillHref(
  base: {
    /** The card's own module — the default target. */
    moduleId: string;
    /** The board's bound log. `null` ⇒ nothing to drill into. */
    logId: string | null;
    /** The widget's manifest `drill:` block, if any. */
    manifestDrill?: WidgetDrill | null;
  },
  target?: DrillTarget,
): string | null {
  if (base.manifestDrill?.enabled === false) return null;

  const logId = target?.logId ?? base.logId;
  if (!logId) return null;

  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(base.manifestDrill?.params ?? {})) {
    if (value !== "") search.set(key, value);
  }
  for (const [key, value] of Object.entries(target?.params ?? {})) {
    // Drop empties so callers can pass a possibly-absent value unguarded — and
    // so an explicit `undefined` can't produce `?activity=undefined`.
    if (value === undefined || value === null || value === "") search.delete(key);
    else search.set(key, String(value));
  }

  const explicitModule = target?.moduleId ?? base.manifestDrill?.module_id ?? null;
  if (!explicitModule) {
    const variant = search.get(DRILL_PARAMS.variant);
    if (variant) return variantHref(logId, variant);
    const activity = search.get(DRILL_PARAMS.activity);
    if (activity) return activityHref(logId, activity);
  }

  const moduleId = explicitModule ?? base.moduleId;
  if (!moduleId) return null;

  const query = search.toString();
  return `${modulePath(logId, moduleId)}${query ? `?${query}` : ""}${target?.hash ?? ""}`;
}

/** The label for the card's "open in module" affordance. */
export function drillLabel(manifestDrill?: WidgetDrill | null): string {
  return manifestDrill?.label ?? "Open in module";
}

/**
 * Read the standard drill params on the receiving side.
 *
 * Panels hand-parsed `useSearchParams()` before; this keeps the names in one
 * place so a rename can't silently break the link between two modules.
 */
export function useDrillParams(): Partial<Record<keyof typeof DRILL_PARAMS, string>> {
  const search = useSearchParams();
  const out: Partial<Record<keyof typeof DRILL_PARAMS, string>> = {};
  for (const [key, param] of Object.entries(DRILL_PARAMS) as [
    keyof typeof DRILL_PARAMS,
    string,
  ][]) {
    const value = search.get(param);
    if (value) out[key] = value;
  }
  return out;
}
