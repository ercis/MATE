"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { useProgressRouter } from "@/lib/use-progress-router";
import { useTheme } from "next-themes";
import {
  Activity,
  AlertTriangle,
  ArrowUp,
  Compass,
  Lightbulb,
  ListChecks,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  Waves,
  Workflow,
  X,
  Zap,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";
import { rawFetch } from "@/lib/api";
import { useAiConfig } from "@/lib/ai-queries";
import { useModules } from "@/lib/queries";
import { useMateChat } from "@/lib/stores/mate-chat";
import { useUi } from "@/lib/stores/ui";
import { useTrack } from "@/lib/analytics/hooks";
import { EV } from "@/lib/analytics/events";
import { useOnboardingState, useUpdateOnboarding } from "@/lib/onboarding-queries";
import {
  NavWidget,
  type ActionTarget,
  type NavTarget,
} from "@/components/mate-ai/nav-widget";

interface Message {
  role: "user" | "assistant";
  content: string;
  isError?: boolean;
  navTargets?: NavTarget[];
  actionTargets?: ActionTarget[];
}

interface RouteResponse {
  intent: string;
  confidence: number;
  targets: NavTarget[];
  actions: ActionTarget[];
}

interface Starter {
  icon: LucideIcon;
  title: string;
  subtitle: string;
}

// Starters shown when there's no process context (landing, settings, etc.).
// These are stated GOALS: each routes through /api/v1/ai/route to the matching
// module/view, so picking one drops the user where the work happens.
const DEFAULT_STARTERS: Starter[] = [
  {
    icon: Zap,
    title: "Show me a bottleneck",
    subtitle: "Open the module that finds the slowest steps",
  },
  {
    icon: Workflow,
    title: "Discover my process model",
    subtitle: "See how your process actually flows",
  },
  {
    icon: Compass,
    title: "Suggest a module for me",
    subtitle: "Not sure where to start? Get a recommendation",
  },
];

// Process-level starters – shown on /processes/{logId} when no module is active.
// These replace the inline "Generate AI overview" and "Check data quality" cards.
const PROCESS_STARTERS: Starter[] = [
  {
    icon: Sparkles,
    title: "Generate an AI overview of this process",
    subtitle: "Cross-module summary of the key findings",
  },
  {
    icon: ShieldCheck,
    title: "Check data quality for this log",
    subtitle: "Surface import or event issues worth fixing first",
  },
  {
    icon: Zap,
    title: "Find bottlenecks across the process",
    subtitle: "Highlight the slowest steps and rework loops",
  },
];

// Per-module starters – these replace each module's inline "Get AI insights" card.
const MODULE_STARTERS: Record<string, Starter[]> = {
  complexity: [
    {
      icon: Sparkles,
      title: "Interpret these complexity metrics",
      subtitle: "Translate entropy and Pentland values into plain language",
    },
    {
      icon: AlertTriangle,
      title: "Flag unusual complexity signals",
      subtitle: "Point out values that deviate from a typical process",
    },
    {
      icon: ListChecks,
      title: "Suggest next analysis steps",
      subtitle: "Where to dig deeper based on these results",
    },
  ],
  performance: [
    {
      icon: Zap,
      title: "Find the worst bottlenecks",
      subtitle: "Activities and handovers dragging cycle time",
    },
    {
      icon: Activity,
      title: "Explain the cycle-time distribution",
      subtitle: "What median, p90, and lead time tell us here",
    },
    {
      icon: ListChecks,
      title: "Suggest performance improvements",
      subtitle: "Prioritised actions based on the KPIs",
    },
  ],
  cv4cdd: [
    {
      icon: Waves,
      title: "Summarise detected drifts",
      subtitle: "What kind of changes were found and when",
    },
    {
      icon: AlertTriangle,
      title: "Highlight high-confidence drifts",
      subtitle: "Which drift points warrant attention first",
    },
    {
      icon: ListChecks,
      title: "Recommend root-cause checks",
      subtitle: "Where to look in the data for each drift",
    },
  ],
  discovery: [
    {
      icon: Workflow,
      title: "Walk me through the process model",
      subtitle: "Main paths from start to end",
    },
    {
      icon: Lightbulb,
      title: "Explain the most common variants",
      subtitle: "Which flows account for most cases",
    },
    {
      icon: AlertTriangle,
      title: "Flag suspicious rework or loops",
      subtitle: "Highlight unusual structures in the model",
    },
  ],
};

function genericModuleStarters(moduleName: string): Starter[] {
  return [
    {
      icon: Sparkles,
      title: `Interpret the ${moduleName} results`,
      subtitle: "Plain-language summary of this module's output",
    },
    {
      icon: AlertTriangle,
      title: `Flag anomalies in ${moduleName}`,
      subtitle: "Point out values that look off compared to a healthy process",
    },
    {
      icon: ListChecks,
      title: "Suggest next analysis steps",
      subtitle: "Where to dig deeper based on these results",
    },
  ];
}

// --------------------------------------------------------------------------
// Sub-components
// --------------------------------------------------------------------------

/** The clickable starter chips. Rendered in the welcome empty state and, for
 *  contexts the user hasn't prompted in yet (or on demand via the lightbulb
 *  toggle), as a strip at the end of an ongoing conversation. */
function StarterList({
  starters,
  disabled,
  onPick,
}: {
  starters: Starter[];
  disabled: boolean;
  onPick: (title: string) => void;
}) {
  return (
    <div className="w-full space-y-2">
      {starters.map((s) => {
        const Icon = s.icon;
        return (
          <button
            key={s.title}
            type="button"
            disabled={disabled}
            onClick={() => onPick(s.title)}
            className={cn(
              "group flex w-full cursor-pointer items-start gap-3 rounded-lg border border-sidebar-border bg-sidebar-accent/30 p-3 text-left transition-colors",
              "hover:border-sidebar-border/80 hover:bg-sidebar-accent/60",
              "disabled:cursor-not-allowed disabled:opacity-40",
            )}
          >
            <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-sidebar text-sidebar-foreground/70 group-hover:text-sidebar-foreground">
              <Icon className="h-3.5 w-3.5" />
            </div>
            <div className="min-w-0 flex-1">
              <div className="text-xs font-medium text-sidebar-foreground">{s.title}</div>
              <div className="mt-0.5 text-[11px] text-sidebar-foreground/55">{s.subtitle}</div>
            </div>
          </button>
        );
      })}
    </div>
  );
}

function StreamingSkeleton() {
  return (
    <div className="space-y-2.5 py-0.5">
      <div
        className="h-2.5 w-4/5 animate-pulse rounded-full bg-sidebar-foreground/15"
        style={{ animationDelay: "0ms" }}
      />
      <div
        className="h-2.5 w-3/5 animate-pulse rounded-full bg-sidebar-foreground/15"
        style={{ animationDelay: "180ms" }}
      />
      <div
        className="h-2.5 w-11/12 animate-pulse rounded-full bg-sidebar-foreground/15"
        style={{ animationDelay: "360ms" }}
      />
    </div>
  );
}

function UserBubble({ content }: { content: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] rounded-2xl rounded-tr-sm bg-sidebar-primary px-3.5 py-2.5 text-sm text-sidebar-primary-foreground shadow-sm">
        <p className="whitespace-pre-wrap break-words leading-relaxed">{content}</p>
      </div>
    </div>
  );
}

function AssistantBubble({
  content,
  isError,
  isStreaming,
}: {
  content: string;
  isError?: boolean;
  isStreaming?: boolean;
}) {
  return (
    <div className="flex items-start gap-2.5">
      <div
        className={cn(
          "mt-1 flex h-5 w-5 shrink-0 items-center justify-center rounded-full",
          isError
            ? "bg-destructive/20 text-destructive"
            : "bg-sidebar-primary/15 text-sidebar-primary",
          isStreaming && !content && "animate-pulse",
        )}
      >
        {isError ? (
          <AlertTriangle className="h-3 w-3" />
        ) : (
          <Sparkles className="h-3 w-3" />
        )}
      </div>
      <div
        className={cn(
          "min-w-0 flex-1 rounded-2xl rounded-tl-sm px-3.5 py-2.5 text-sm shadow-sm",
          isError
            ? "border border-destructive/20 bg-destructive/10 text-destructive"
            : "bg-sidebar-accent/60 text-sidebar-foreground",
        )}
      >
        {isStreaming && content === "" ? (
          <StreamingSkeleton />
        ) : (
          <>
            <p className="whitespace-pre-wrap break-words leading-relaxed">{content}</p>
            {isStreaming && (
              <span
                aria-hidden
                className="ml-0.5 inline-block h-[0.85em] w-0.5 translate-y-[2px] animate-pulse rounded-sm bg-current opacity-60"
              />
            )}
          </>
        )}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------
// Main sidebar
// --------------------------------------------------------------------------

// Detect process / module ids from the current URL so the chat can be
// grounded in cached module outputs without the user pasting context in.
const PROCESS_RE = /\/processes\/([^/?#]+)(?:\/modules\/([^/?#]+))?/;

function deriveChatContext(pathname: string | null):
  | { current_path?: string; log_id?: string; module_ids?: string[] }
  | undefined {
  if (!pathname) return undefined;
  // Always send the current route so the assistant is location-aware and the
  // router can drop a shortcut to the page the user is already on.
  const ctx: { current_path: string; log_id?: string; module_ids?: string[] } = {
    current_path: pathname,
  };
  const m = pathname.match(PROCESS_RE);
  if (m) {
    const log_id = decodeURIComponent(m[1]!);
    // Reserved sub-routes like /processes/import or /processes/new aren't real
    // log ids – skip them so the chat doesn't try to load nonexistent state.
    if (log_id !== "import" && log_id !== "new") {
      ctx.log_id = log_id;
      const module_id = m[2] ? decodeURIComponent(m[2]) : null;
      ctx.module_ids = module_id ? [module_id] : [];
    }
  }
  return ctx;
}

// Short, client-side line shown above a chip when we skip the chat LLM (no
// tokens). References the real destination/action so it reads naturally.
function confirmationText(targets: NavTarget[], actions: ActionTarget[]): string {
  if (targets.length) return `Here you go – use the shortcut below to open ${targets[0]!.label}.`;
  if (actions.length) return `Sure – use the button below to ${actions[0]!.label.toLowerCase()}.`;
  return "Done.";
}

export function MateAiSidebar() {
  const open = useUi((s) => s.mateOpen);
  const setOpen = useUi((s) => s.setMateOpen);
  const { data: aiConfig } = useAiConfig();
  const pathname = usePathname();
  const chatContext = useMemo(() => deriveChatContext(pathname), [pathname]);

  const activeModuleId = chatContext?.module_ids?.[0];
  const { data: modules } = useModules(chatContext?.log_id ?? null);
  const activeModule = useMemo(
    () => (activeModuleId ? modules?.find((m) => m.id === activeModuleId) : undefined),
    [activeModuleId, modules],
  );

  const starters = useMemo<Starter[]>(() => {
    if (activeModuleId) {
      return (
        MODULE_STARTERS[activeModuleId] ??
        genericModuleStarters(activeModule?.name ?? "this module")
      );
    }
    if (chatContext?.log_id) return PROCESS_STARTERS;
    return DEFAULT_STARTERS;
  }, [activeModuleId, activeModule, chatContext]);

  // Key for the "has the user already prompted here?" bookkeeping. Scoped to
  // exactly what drives the starter set: module page > process page > global.
  const suggestionContextKey = useMemo(() => {
    if (activeModuleId) return `module:${chatContext?.log_id ?? ""}:${activeModuleId}`;
    if (chatContext?.log_id) return `process:${chatContext.log_id}`;
    return "default";
  }, [activeModuleId, chatContext]);

  const usedSuggestionContexts = useMateChat((s) => s.usedSuggestionContexts);
  const markSuggestionsUsed = useMateChat((s) => s.markSuggestionsUsed);
  const resetSuggestionsUsage = useMateChat((s) => s.resetSuggestionsUsage);

  // null = automatic (show while this context hasn't been prompted in yet);
  // true/false = explicit user choice via the lightbulb toggle in the header.
  const [suggestionsOverride, setSuggestionsOverride] = useState<boolean | null>(null);
  // Moving to another context drops the manual override back to automatic.
  useEffect(() => setSuggestionsOverride(null), [suggestionContextKey]);

  const suggestionsVisible =
    suggestionsOverride ?? !usedSuggestionContexts[suggestionContextKey];

  const [draft, setDraft] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [streamingContent, setStreamingContent] = useState<string | null>(null);
  const track = useTrack();
  const router = useProgressRouter();
  const { setTheme } = useTheme();
  const updateOnboarding = useUpdateOnboarding();
  const { data: onboarding } = useOnboardingState();

  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const onNavigate = (target: NavTarget) => {
    track(EV.AI_NAV_CLICKED, { target_id: target.id, kind: target.kind });
    // Keep the sidebar open across navigation (the platform layout persists, so
    // the conversation stays put) – the user can keep chatting after the jump.
    router.push(target.href);
  };

  // Apply a whitelisted settings change on click. An EXPLICIT switch (no dynamic
  // store access) is the client-side half of the safety boundary: only the
  // settings enumerated here can ever be touched. useUi changes auto-persist.
  const applyUiSetting = (setting: string, value: string | boolean) => {
    const s = useUi.getState();
    switch (setting) {
      case "notifications_muted":
        return s.setNotificationsMuted(value as boolean);
      case "confidential_only":
        return s.setConfidentialOnly(value as boolean);
      case "sidebar_collapsed":
        // External contract stays "collapsed"; internally that maps to unpinned
        // (auto mode). collapsed=false → pin the sidebar open.
        return s.setSidebarPinned(!(value as boolean));
      case "show_unavailable_modules":
        return s.setShowUnavailableModules(value as boolean);
      case "show_disabled_modules":
        return s.setShowDisabledModules(value as boolean);
      case "date_format":
        return s.setDateFormat(value as "iso" | "us" | "eu");
      case "timezone":
        return s.setTimezone(value as string);
      case "csv_delimiter":
        return s.setCsvDelimiter(value as "," | ";" | "\t" | "|");
      case "csv_timestamp_format":
        return s.setCsvTimestampFormat(value as string);
    }
  };

  const onAction = (action: ActionTarget) => {
    track(EV.AI_ACTION_APPLIED, { setting: action.setting, target: action.target });
    if (action.target === "theme") {
      setTheme(String(action.value));
    } else if (action.target === "onboarding") {
      if (action.setting === "experience_level" && onboarding) {
        // Preserve the completed flag – only re-tune the proficiency level.
        updateOnboarding.mutate({
          completed: onboarding.completed,
          experience_level: action.value as "beginner" | "intermediate" | "expert",
        });
      }
    } else {
      applyUiSetting(action.setting, action.value);
    }
  };

  const isStreaming = streamingContent !== null;
  const hasMessages = messages.length > 0 || isStreaming;
  const isConfigured = Boolean(aiConfig?.selected_provider && aiConfig?.selected_model);

  // Scroll to bottom whenever content changes (or the suggestion strip pops
  // in at the end of the conversation – it must be visible, not below the fold)
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, streamingContent, suggestionsVisible]);

  // Focus textarea when sidebar opens
  useEffect(() => {
    if (open) setTimeout(() => textareaRef.current?.focus(), 310);
  }, [open]);

  const submit = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || isStreaming) return;

    // The user has now prompted in this context – its starters stop showing
    // automatically (they stay one lightbulb-click away).
    markSuggestionsUsed(suggestionContextKey);
    setSuggestionsOverride(null);

    track(EV.AI_CHAT_SENT, {
      provider: aiConfig?.selected_provider ?? null,
      model: aiConfig?.selected_model ?? null,
      message_chars: trimmed.length,
      turn: messages.length + 1,
      has_context: !!chatContext,
    });

    const userMsg: Message = { role: "user", content: trimmed };
    const history: Message[] = [...messages, userMsg];
    setMessages(history);
    setDraft("");

    // Only send non-error messages to the API
    const apiMessages = history
      .filter((m) => !m.isError)
      .map((m) => ({ role: m.role, content: m.content }));

    // 1) Classify first (cheap nano). This decides whether the chat LLM is even
    //    needed and lets the chat reply stay concise when a chip is shown.
    let route: RouteResponse | null = null;
    try {
      const r = await rawFetch("/api/v1/ai/route", {
        method: "POST",
        json: chatContext ? { message: trimmed, context: chatContext } : { message: trimmed },
      });
      route = r.ok ? ((await r.json()) as RouteResponse) : null;
    } catch {
      route = null;
    }

    const targets = route?.targets ?? [];
    const actions = route?.actions ?? [];
    if (targets.length) {
      track(EV.AI_NAV_SUGGESTED, {
        intent: route?.intent,
        confidence: route?.confidence,
        count: targets.length,
        target_ids: targets.map((t) => t.id),
      });
    }
    if (actions.length) {
      track(EV.AI_ACTION_SUGGESTED, { settings: actions.map((a) => a.setting) });
    }

    // 2) Command-style turns skip the chat LLM entirely and show a templated line
    //    + chip(s): an available navigation target, or ANY settings action (a
    //    setting chip never needs an LLM answer – just like a nav chip).
    const navChip = targets[0];
    const pureNavigate = route?.intent === "navigate" && navChip?.available === true;
    if (pureNavigate || actions.length > 0) {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: confirmationText(targets, actions),
          navTargets: targets.length ? targets : undefined,
          actionTargets: actions.length ? actions : undefined,
        },
      ]);
      return;
    }

    // 3) Run the chat LLM. Targets/actions are already known (route awaited), so
    //    attach them on finalise; pass a nav_hint so the answer stays concise.
    setStreamingContent("");
    let full = "";
    let finalised = false;

    const finalise = (content: string, isError = false) => {
      if (finalised) return;
      finalised = true;
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content,
          isError,
          navTargets: !isError && targets.length ? targets : undefined,
          actionTargets: !isError && actions.length ? actions : undefined,
        },
      ]);
      setStreamingContent(null);
    };

    const chatBody: Record<string, unknown> = { messages: apiMessages };
    if (chatContext) chatBody.context = chatContext;
    if (navChip) {
      chatBody.nav_hint = {
        label: navChip.label,
        intent: route?.intent === "both" ? "both" : "navigate",
      };
    }

    try {
      const res = await rawFetch("/api/v1/ai/chat", { method: "POST", json: chatBody });

      if (!res.ok) {
        let detail: string;
        try {
          const body = await res.json();
          detail =
            typeof body?.detail === "string" ? body.detail : JSON.stringify(body?.detail ?? body);
        } catch {
          detail = await res.text();
        }
        finalise(detail, true);
        return;
      }

      const reader = res.body!.getReader();
      const decoder = new TextDecoder();
      let buf = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() ?? "";

        for (const line of lines) {
          if (!line.startsWith("data:")) continue;
          try {
            const evt = JSON.parse(line.slice(5).trim()) as {
              delta?: string;
              done?: boolean;
              error?: string;
            };
            if (evt.delta) {
              full += evt.delta;
              setStreamingContent(full);
            }
            if (evt.done) finalise(full);
            if (evt.error) finalise(evt.error, true);
          } catch {
            // malformed SSE chunk
          }
        }
      }

      // Safety: if stream ended without a done/error event
      if (!finalised) finalise(full || "No response received.");
    } catch (err) {
      finalise((err as Error).message, true);
    }
  };

  return (
    <aside
      aria-label="MATE AI assistant"
      aria-hidden={!open}
      className={cn(
        "flex h-full shrink-0 flex-col overflow-hidden border-l border-white/10 [border-top-color:var(--glass-refraction-top)] bg-sidebar/85 text-sidebar-foreground backdrop-blur-xl backdrop-saturate-150 transition-[width] duration-300 ease-in-out supports-[backdrop-filter]:bg-sidebar/70",
        open ? "w-[380px]" : "w-0 border-l-0",
      )}
    >
      <div className="flex h-full w-[380px] min-w-[380px] flex-col">
        {/* Header */}
        <header className="flex items-center gap-2.5 border-b border-sidebar-border px-4 py-3">
          <div
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-sidebar-primary text-sidebar-primary-foreground"
            aria-hidden
          >
            <Sparkles className="h-4 w-4" />
          </div>
          <div className="flex min-w-0 flex-1 items-center gap-2">
            <span className="truncate text-sm font-semibold tracking-tight">MATE AI</span>
            <Badge
              variant="secondary"
              className="h-4 border-0 bg-sidebar-accent px-1.5 text-[10px] font-medium uppercase tracking-wide text-sidebar-accent-foreground/70"
            >
              Beta
            </Badge>
          </div>
          {hasMessages && (
            <>
              <button
                type="button"
                aria-label={
                  suggestionsVisible ? "Hide suggestions" : "Show suggestions"
                }
                aria-pressed={suggestionsVisible}
                title="Suggestions for analysis"
                onClick={() => setSuggestionsOverride(!suggestionsVisible)}
                className={cn(
                  "flex h-7 w-7 cursor-pointer items-center justify-center rounded-md transition-colors hover:bg-sidebar-accent hover:text-sidebar-foreground",
                  suggestionsVisible
                    ? "text-sidebar-primary"
                    : "text-sidebar-foreground/50",
                )}
              >
                <Lightbulb className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                aria-label="New conversation"
                onClick={() => {
                  setMessages([]);
                  setStreamingContent(null);
                  // A fresh conversation gets fresh starters everywhere.
                  resetSuggestionsUsage();
                  setSuggestionsOverride(null);
                }}
                className="flex h-7 w-7 cursor-pointer items-center justify-center rounded-md text-sidebar-foreground/50 transition-colors hover:bg-sidebar-accent hover:text-sidebar-foreground"
              >
                <RotateCcw className="h-3.5 w-3.5" />
              </button>
            </>
          )}
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="Close MATE AI"
            onClick={() => setOpen(false)}
            className="h-8 w-8 cursor-pointer text-sidebar-foreground/60 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
          >
            <X className="h-4 w-4" />
          </Button>
        </header>

        {/* Message area */}
        <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
          {!hasMessages ? (
            /* Welcome / empty state */
            <div className="flex flex-col items-center px-5 pt-12 pb-6 text-center">
              <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-sidebar-primary/10 text-sidebar-primary">
                <Sparkles className="h-6 w-6" />
              </div>
              <h2 className="text-base font-semibold tracking-tight">How can I help?</h2>
              <p className="mt-1.5 text-xs text-sidebar-foreground/60">
                {activeModule
                  ? `Ask me about the ${activeModule.name} results, or pick a starter below.`
                  : chatContext?.log_id
                  ? "Ask me about this process, or pick a starter below."
                  : "Ask me anything about your processes, variants, or modules."}
              </p>

              {!isConfigured && aiConfig !== undefined && (
                <div className="mt-4 w-full rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2.5 text-left text-xs text-amber-700 dark:text-amber-400">
                  No AI model configured.{" "}
                  <a
                    href="/settings/ai"
                    className="font-medium underline underline-offset-2"
                  >
                    Settings → AI
                  </a>{" "}
                  to set one up.
                </div>
              )}

              <div className="mt-6 w-full">
                <StarterList
                  starters={starters}
                  disabled={!isConfigured || isStreaming}
                  onPick={(title) => void submit(title)}
                />
              </div>
            </div>
          ) : (
            /* Conversation */
            <div className="flex flex-col gap-4 px-4 py-4">
              {messages.map((msg, i) =>
                msg.role === "user" ? (
                  <UserBubble key={i} content={msg.content} />
                ) : (
                  <div key={i}>
                    <AssistantBubble content={msg.content} isError={msg.isError} />
                    {((msg.navTargets?.length ?? 0) > 0 ||
                      (msg.actionTargets?.length ?? 0) > 0) && (
                      <div className="pl-[30px]">
                        <NavWidget
                          targets={msg.navTargets}
                          actions={msg.actionTargets}
                          onNavigate={onNavigate}
                          onAction={onAction}
                        />
                      </div>
                    )}
                  </div>
                ),
              )}
              {isStreaming && (
                <AssistantBubble content={streamingContent ?? ""} isStreaming />
              )}
              {/* Context starters mid-conversation: automatic on the first
                  visit to a context that hasn't been prompted in yet, or on
                  demand via the lightbulb toggle in the header. */}
              {suggestionsVisible && (
                <div>
                  <div className="mb-2 flex items-center gap-1.5 px-0.5 text-[11px] font-medium text-sidebar-foreground/50">
                    <Lightbulb className="h-3 w-3" aria-hidden />
                    <span>
                      Suggestions
                      {activeModule
                        ? ` for ${activeModule.name}`
                        : chatContext?.log_id
                        ? " for this process"
                        : ""}
                    </span>
                  </div>
                  <StarterList
                    starters={starters}
                    disabled={!isConfigured || isStreaming}
                    onPick={(title) => void submit(title)}
                  />
                </div>
              )}
              {/* Bottom anchor for auto-scroll */}
              <div className="h-px" />
            </div>
          )}
        </div>

        {/* Input */}
        <div className="border-t border-sidebar-border px-3 py-3">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void submit(draft);
            }}
            className="relative rounded-xl border border-input bg-background shadow-sm focus-within:ring-1 focus-within:ring-ring/40"
          >
            <textarea
              ref={textareaRef}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void submit(draft);
                }
              }}
              placeholder={
                isStreaming
                  ? "Waiting for response…"
                  : "Ask MATE AI… (Enter to send)"
              }
              rows={2}
              disabled={isStreaming}
              className={cn(
                "block w-full resize-none rounded-xl bg-transparent px-3 py-2.5 pr-11 text-sm text-foreground placeholder:text-muted-foreground",
                "min-h-[56px] max-h-[160px] focus:outline-none",
                "disabled:cursor-not-allowed disabled:opacity-50",
              )}
            />
            <Button
              type="submit"
              size="icon"
              disabled={!draft.trim() || isStreaming}
              aria-label="Send message"
              className="absolute right-2 bottom-2 h-7 w-7 cursor-pointer rounded-md"
            >
              <ArrowUp className="h-3.5 w-3.5" />
            </Button>
          </form>
          <p className="mt-2 px-1 text-[10px] text-sidebar-foreground/40">
            MATE AI can make mistakes. Verify important details.
          </p>
        </div>
      </div>
    </aside>
  );
}
