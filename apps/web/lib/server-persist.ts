"use client";

import { useEffect, useRef } from "react";
import type { StoreApi } from "zustand";

import { getPreference, putPreference } from "@/lib/preferences";

/** The serialisable data slice of a store – its action functions stripped. */
export function pickData<T extends object>(state: T): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(state as Record<string, unknown>).filter(
      ([, v]) => typeof v !== "function",
    ),
  );
}

function isEmpty(o: Record<string, unknown> | null | undefined): boolean {
  return !o || Object.keys(o).length === 0;
}

/** Read a zustand-persisted localStorage blob's `state` slice, if present. */
function readLegacyState(key: string): Record<string, unknown> | null {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as unknown;
    // zustand persist wraps as `{ state, version }`; tolerate a bare object too.
    const state =
      parsed && typeof parsed === "object" && "state" in parsed
        ? (parsed as { state: unknown }).state
        : parsed;
    return state && typeof state === "object"
      ? (state as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

function clearLegacyState(key: string): void {
  try {
    localStorage.removeItem(key);
  } catch {
    /* ignore */
  }
}

interface Options<T extends object> {
  store: StoreApi<T>;
  /** Preference key (must be allowlisted server-side). */
  key: string;
  /** Only hydrate/save while signed in. */
  enabled: boolean;
  /** Identifies the account; changing it re-hydrates the new user's state. */
  userKey: string | null;
  /** Current serialisable snapshot to persist. */
  read: () => Record<string, unknown>;
  /** Merge a server blob (possibly empty) over store defaults. */
  apply: (data: Record<string, unknown>) => void;
  /** Old localStorage key to import-then-clear once, for a seamless move off
   *  per-browser persistence. Only used when the server has nothing saved. */
  legacyKey?: string;
  debounceMs?: number;
}

/**
 * Binds a zustand store to per-user server state: hydrate from the server on
 * sign-in, then debounce-save subsequent changes back. Replaces per-browser
 * localStorage persistence so prefs are per-account (and follow the user
 * across devices) instead of bleeding between accounts on a shared browser.
 */
export function useServerPersistedStore<T extends object>({
  store,
  key,
  enabled,
  userKey,
  read,
  apply,
  legacyKey,
  debounceMs = 600,
}: Options<T>): void {
  const hydrated = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastSaved = useRef<string>("");

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let unsub: (() => void) | undefined;
    hydrated.current = false;

    const save = (immediate: boolean) => {
      if (!hydrated.current) return;
      const json = JSON.stringify(read());
      if (json === lastSaved.current) return; // nothing actually changed
      if (timer.current) clearTimeout(timer.current);
      const commit = () => {
        lastSaved.current = json;
        void putPreference(key, JSON.parse(json) as Record<string, unknown>, immediate);
      };
      if (immediate) commit();
      else timer.current = setTimeout(commit, debounceMs);
    };

    void (async () => {
      const server = await getPreference(key);
      if (cancelled) return;
      let blob = server ?? {};
      // One-time migration: if the server has nothing for this user yet, seed
      // it from their pre-existing localStorage so the move off per-browser
      // persistence is seamless. The legacy key is always cleared afterward so
      // it can't be re-imported (or bleed to another account on this browser).
      let migrated = false;
      if (legacyKey && isEmpty(blob)) {
        const legacy = readLegacyState(legacyKey);
        if (!isEmpty(legacy)) {
          blob = legacy!;
          migrated = true;
        }
      }
      // Always apply (server merged over defaults inside `apply`) so a new
      // account on this browser resets to defaults rather than inheriting the
      // previous account's in-memory state.
      apply(blob);
      // Record the post-hydration snapshot so the first change – not the
      // hydration itself – is what triggers a save.
      const snapshot = JSON.stringify(read());
      lastSaved.current = snapshot;
      hydrated.current = true;
      unsub = store.subscribe(() => save(false));
      if (migrated) {
        // Persist the imported baseline now (worst case – a failed PUT – just
        // degrades to starting from defaults, never worse than no migration).
        void putPreference(key, JSON.parse(snapshot) as Record<string, unknown>, false);
      }
      if (legacyKey) clearLegacyState(legacyKey);
    })();

    // Flush pending edits before the tab is hidden / unloaded.
    const onHide = () => save(true);
    document.addEventListener("visibilitychange", onHide);
    window.addEventListener("pagehide", onHide);

    return () => {
      cancelled = true;
      save(true);
      if (timer.current) clearTimeout(timer.current);
      unsub?.();
      document.removeEventListener("visibilitychange", onHide);
      window.removeEventListener("pagehide", onHide);
    };
    // read/apply are stable closures over the store; re-running on their
    // identity would reset hydration every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, userKey, key]);
}
