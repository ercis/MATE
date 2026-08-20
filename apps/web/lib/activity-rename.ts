/**
 * Display-only renames for activity values.
 *
 * The Activities tab persists a map `{ raw_name: display_name }` inside
 * `EventLog.column_overrides.activity_labels`. Analytics modules and the
 * backend storage layer keep using raw activity names; only the rendering
 * surfaces (Events table, Variants list, Variant detail) translate via
 * these helpers so the proxy stays cosmetic.
 */

import type { EventLogDetail } from "@/lib/api-types";

export type ActivityRenameMap = Readonly<Record<string, string>>;

const EMPTY: ActivityRenameMap = Object.freeze({});

export function getActivityRenameMap(
  log: EventLogDetail | null | undefined,
): ActivityRenameMap {
  const raw = log?.column_overrides?.activity_labels;
  if (!raw || typeof raw !== "object") return EMPTY;
  return raw as ActivityRenameMap;
}

/** Translate a raw activity name to its display label, falling back to the
 * raw value when no override exists or the override is blank.
 */
export function displayActivity(raw: string, map: ActivityRenameMap): string {
  const next = map[raw];
  if (typeof next === "string" && next.trim().length > 0) return next;
  return raw;
}

/** Convenience for the common case of mapping a sequence at once. */
export function displayActivities(
  activities: readonly string[],
  map: ActivityRenameMap,
): string[] {
  return activities.map((a) => displayActivity(a, map));
}
