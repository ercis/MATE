"use client";

import { api } from "@/lib/api";

/**
 * Per-user client-state blobs (`/api/v1/preferences/{key}`). Source of truth
 * for the device-independent zustand stores (ui, viz). Both helpers are
 * best-effort: a failure (e.g. a request firing before auth settles) falls
 * back to the store defaults rather than throwing.
 */

export async function getPreference(
  key: string,
): Promise<Record<string, unknown> | null> {
  try {
    return await api<Record<string, unknown>>(`/api/v1/preferences/${key}`);
  } catch {
    return null;
  }
}

export async function putPreference(
  key: string,
  value: Record<string, unknown>,
  keepalive = false,
): Promise<void> {
  try {
    // `keepalive` lets the final flush survive a tab close / navigation.
    await api(`/api/v1/preferences/${key}`, { method: "PUT", json: value, keepalive });
  } catch {
    /* best-effort – a dropped preference save is non-fatal */
  }
}
