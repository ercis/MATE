"use client";

import { createContext, useContext, useMemo, type ReactNode } from "react";

/**
 * Which settings bucket a rendered visualization reads and writes.
 *
 * Render settings (layout direction, thresholds, edge labels, node positions…)
 * live in a zustand store keyed by scope. A module *panel* uses its module id,
 * so every view of that module on that log shares one set of settings — which
 * is right for a panel.
 *
 * It was wrong for a dashboard card. `modules/discovery`'s process-map widget
 * mounted the panel's settings provider directly, so a card silently inherited
 * whatever the user had last set in the panel and offered no way to change it:
 * two cards of the same widget could never differ, and neither could differ
 * from the panel.
 *
 * A card therefore gets its own scope, keyed by its placement id. The card
 * provides it through context so widgets need no new prop — a widget that
 * builds its own settings provider picks the scope up automatically, and the
 * same component keeps working inside a panel, where there is no card context
 * and the panel scope applies.
 *
 * Card scopes are persisted in the card's own `config`, not in the per-user
 * preference blob: they have to travel with a shared or exported board, and
 * keying a user-wide blob by card id would grow it without bound.
 */
const CardScopeContext = createContext<string | null>(null);

/** Scope for a module panel: shared across that module's views on a log. */
export function panelScope(moduleId: string): string {
  // Identical to the value used before scopes existed, so every already-
  // persisted panel setting keeps resolving with no store migration.
  return moduleId;
}

/** Scope for one placed card. Prefixed so it can be told apart from a panel
 * scope — card scopes are stripped from the per-user preference blob. */
export function cardScope(cardId: string): string {
  return `card:${cardId}`;
}

export function isCardScope(scope: string): boolean {
  return scope.startsWith("card:");
}

export function CardScopeProvider({ cardId, children }: { cardId: string; children: ReactNode }) {
  const scope = useMemo(() => cardScope(cardId), [cardId]);
  return <CardScopeContext.Provider value={scope}>{children}</CardScopeContext.Provider>;
}

/**
 * The current card's scope, or `null` outside a dashboard card.
 *
 * Resolve settings scope as `explicitProp ?? useCardScope() ?? panelScope(id)`
 * so the same component works in a card and in a panel.
 */
export function useCardScope(): string | null {
  return useContext(CardScopeContext);
}
