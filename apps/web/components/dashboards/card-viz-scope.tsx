"use client";

import { useEffect, useRef, type ReactNode } from "react";

import { CardScopeProvider, cardScope } from "@/lib/dashboards/card-scope";
import { useVizSettings } from "@/lib/stores/visualization-settings";

/** Reserved key holding a card's render settings inside its placement config.
 * Same convention as the widget-filter key: the platform owns keys prefixed
 * `__ff_`, and strips them before the widget sees its own options. */
export const RENDER_CONFIG_KEY = "__ff_render__";

/** How long to wait after a settings change before writing it back. Dragging a
 * threshold slider churns the store on every frame; the board's own autosave
 * then debounces again on top. */
const WRITE_BACK_MS = 400;

interface StoredRender {
  perViz?: unknown;
  positions?: unknown;
}

/**
 * Gives a card its own render-settings bucket, and persists it on the card.
 *
 * Two problems, one component:
 *
 * 1. **Isolation.** Render settings are keyed by scope, and a widget that
 *    mounts its module's settings provider (discovery's process map does)
 *    would otherwise land in the *panel's* bucket — so the card silently
 *    inherited whatever the user last set in the panel and could never differ
 *    from it. `CardScopeProvider` redirects it to `card:<placement id>`.
 *
 * 2. **Persistence.** That bucket must not go into the per-user preference
 *    blob (it is stripped there, see `withoutCardScopes`): a card's settings
 *    belong to the board, so they have to travel with a share or an export and
 *    must not accumulate under dead card ids. They ride the placement's own
 *    `config` instead, which is already saved by the board's autosave.
 */
export function CardVizScope({
  cardId,
  logId,
  config,
  onConfigChange,
  children,
}: {
  cardId: string;
  logId: string | null;
  config: Record<string, unknown>;
  onConfigChange: (config: Record<string, unknown>) => void;
  children: ReactNode;
}) {
  const scope = cardScope(cardId);
  const configRef = useRef(config);
  configRef.current = config;
  const onChangeRef = useRef(onConfigChange);
  onChangeRef.current = onConfigChange;

  // Seed the store from the card's stored settings, once per (card, log).
  // Only fills buckets that are still empty, so a re-mount (scrolling the card
  // out and back) can't clobber edits the user just made.
  const seeded = useRef<string | null>(null);
  useEffect(() => {
    if (!logId) return;
    const key = `${logId}:${scope}`;
    if (seeded.current === key) return;
    seeded.current = key;
    const stored = configRef.current[RENDER_CONFIG_KEY] as StoredRender | undefined;
    if (!stored || typeof stored !== "object") return;
    useVizSettings.setState((s) => {
      const log = s.perLog[logId] ?? {};
      const pos = s.positions[logId] ?? {};
      return {
        perLog:
          stored.perViz && !log[scope]
            ? { ...s.perLog, [logId]: { ...log, [scope]: stored.perViz as never } }
            : s.perLog,
        positions:
          stored.positions && !pos[scope]
            ? { ...s.positions, [logId]: { ...pos, [scope]: stored.positions as never } }
            : s.positions,
      };
    });
  }, [logId, scope]);

  // Write changes back onto the card, debounced.
  useEffect(() => {
    if (!logId) return;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const unsubscribe = useVizSettings.subscribe((state) => {
      const perViz = state.perLog[logId]?.[scope];
      const positions = state.positions[logId]?.[scope];
      if (!perViz && !positions) return;
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        const next: StoredRender = {};
        if (perViz) next.perViz = perViz;
        if (positions) next.positions = positions;
        const current = configRef.current[RENDER_CONFIG_KEY];
        // Cheap equality — these are small plain objects, and skipping a
        // no-op write keeps the board's autosave from firing for nothing.
        if (JSON.stringify(current) === JSON.stringify(next)) return;
        onChangeRef.current({ ...configRef.current, [RENDER_CONFIG_KEY]: next });
      }, WRITE_BACK_MS);
    });
    return () => {
      if (timer) clearTimeout(timer);
      unsubscribe();
    };
  }, [logId, scope]);

  return <CardScopeProvider cardId={cardId}>{children}</CardScopeProvider>;
}

/** Strip the reserved render key so a widget only ever sees its own options. */
export function configWithoutRender(
  config: Record<string, unknown>,
): Record<string, unknown> {
  if (!(RENDER_CONFIG_KEY in config)) return config;
  const { [RENDER_CONFIG_KEY]: _omit, ...rest } = config;
  return rest;
}
