"use client";

import { create } from "zustand";

/**
 * Ephemeral MATE AI chat state that must outlive the empty-state check but
 * mirror the conversation's lifetime (the conversation itself is component
 * state in `MateAiSidebar`, kept alive by the persistent platform layout).
 *
 * Tracks which chat contexts (process page, module page, …) the user has
 * already prompted in, so the "suggestions for analysis" starters can
 * reappear when the chat is opened in a context that hasn't been used yet –
 * sending a message about log evolution must not hide the performance
 * module's starters. Deliberately NOT persisted: on a full reload the
 * conversation resets too, and the welcome state shows starters anyway.
 */
interface MateChatState {
  /** Context keys (see `suggestionContextKey` in the sidebar) the user has
   *  sent at least one message in. */
  usedSuggestionContexts: Record<string, true>;
  markSuggestionsUsed: (contextKey: string) => void;
  /** Called on "New conversation" – a fresh chat gets fresh suggestions. */
  resetSuggestionsUsage: () => void;
}

export const useMateChat = create<MateChatState>((set) => ({
  usedSuggestionContexts: {},
  markSuggestionsUsed: (contextKey) =>
    set((s) =>
      s.usedSuggestionContexts[contextKey]
        ? s
        : {
            usedSuggestionContexts: { ...s.usedSuggestionContexts, [contextKey]: true },
          },
    ),
  resetSuggestionsUsage: () => set({ usedSuggestionContexts: {} }),
}));
