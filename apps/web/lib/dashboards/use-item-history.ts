"use client";

import { useCallback, useRef, useState } from "react";

import type { DashboardItem } from "@/lib/dashboard-queries";

/** How many steps back you can go. */
const LIMIT = 50;
/** Same-label edits closer together than this collapse into one step, so
 * holding an arrow key doesn't fill the stack with single-cell nudges. */
const COALESCE_MS = 500;

export interface ItemHistory {
  /** Apply a new item list AND record it as an undoable step.
   *
   * `label` groups consecutive edits of the same kind ("move", "resize",
   * "config"…) — pass a *specific* one, since two different edits sharing a
   * label within the coalesce window would merge into a single step. */
  commit: (next: DashboardItem[], label: string) => void;
  undo: () => void;
  redo: () => void;
  canUndo: boolean;
  canRedo: boolean;
  /** True while an undo/redo is being applied. The canvas checks this to
   * suppress the grid's echo — see the note below. */
  isApplying: () => boolean;
}

/**
 * Undo/redo over a board's item list.
 *
 * Every edit autosaves, so without this a mis-drag or a stray Delete is
 * permanent — there is no "close without saving" to fall back on.
 *
 * Two rules make this correct here, and both are easy to get wrong:
 *
 * 1. **Push from the mutation call site, never from an effect on `items`.**
 *    An effect-based history would also capture react-grid-layout's mount echo
 *    and every autosave round-trip, so the stack would fill with steps the user
 *    never made and undo would appear to do nothing.
 *
 * 2. **Guard the grid's echo while applying.** An undo swaps `items`, the
 *    layout re-derives, and RGL fires `onLayoutChange` with the restored
 *    geometry. Recording that as a fresh step would push the state you just
 *    undid back onto the stack, making undo unreachable. `isApplying()` is how
 *    the canvas tells that echo apart from a real edit.
 */
export function useItemHistory(
  items: DashboardItem[],
  setItems: (next: DashboardItem[]) => void,
): ItemHistory {
  const past = useRef<DashboardItem[][]>([]);
  const future = useRef<DashboardItem[][]>([]);
  const lastLabel = useRef<string | null>(null);
  const lastAt = useRef(0);
  const applying = useRef(false);
  // Mirrors `items` so `commit` can snapshot the pre-edit state without taking
  // `items` as a dependency (which would re-create it on every board render).
  const currentRef = useRef(items);
  currentRef.current = items;
  // Only drives button enablement; the refs above are the real state.
  const [[canUndo, canRedo], setFlags] = useState<[boolean, boolean]>([false, false]);
  const syncFlags = useCallback(
    () => setFlags([past.current.length > 0, future.current.length > 0]),
    [],
  );

  const commit = useCallback(
    (next: DashboardItem[], label: string) => {
      const now = Date.now();
      const coalesce = lastLabel.current === label && now - lastAt.current < COALESCE_MS;
      if (!coalesce) {
        past.current.push(currentRef.current);
        if (past.current.length > LIMIT) past.current.shift();
      }
      lastLabel.current = label;
      lastAt.current = now;
      // A new edit invalidates anything that was undone.
      future.current = [];
      setItems(next);
      syncFlags();
    },
    [setItems, syncFlags],
  );

  const step = useCallback(
    (from: DashboardItem[][], to: DashboardItem[][]) => {
      const snapshot = from.pop();
      if (!snapshot) return;
      to.push(currentRef.current);
      // Break any coalescing run: the next edit must start a fresh step rather
      // than merging into the one we just stepped over.
      lastLabel.current = null;
      applying.current = true;
      setItems(snapshot);
      // Cleared after the layout has re-derived and the grid has echoed.
      requestAnimationFrame(() => {
        applying.current = false;
      });
      syncFlags();
    },
    [setItems, syncFlags],
  );

  const undo = useCallback(() => step(past.current, future.current), [step]);
  const redo = useCallback(() => step(future.current, past.current), [step]);
  const isApplying = useCallback(() => applying.current, []);

  return { commit, undo, redo, canUndo, canRedo, isApplying };
}
