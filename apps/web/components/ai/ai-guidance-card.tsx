"use client";

import { useCallback, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Info,
  Loader2,
  RefreshCw,
  Sparkles,
  TriangleAlert,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { useAiConfig } from "@/lib/ai-queries";
import {
  streamModuleGuidance,
  useModuleGuidance,
  useProcessGuidance,
  useImportQualityGuidance,
  useRegenerateModuleGuidance,
  useRegenerateProcessGuidance,
  type GuidanceBody,
  type GuidanceFlag,
  type GuidanceResponse,
  type GuidanceSeverity,
} from "@/lib/ai-guidance";
import { cn } from "@/lib/cn";

type Kind = "module" | "process" | "import-quality";

interface Props {
  kind: Kind;
  logId: string;
  moduleId?: string;
  /** Optional override for the call-to-action button label. */
  ctaLabel?: string;
  /** Optional className applied to the outer <Card>. */
  className?: string;
}

export function AiGuidanceCard({ kind, logId, moduleId, ctaLabel, className }: Props) {
  if (kind === "module") {
    if (!moduleId) {
      throw new Error("AiGuidanceCard kind='module' requires moduleId");
    }
    return (
      <ModuleGuidance
        logId={logId}
        moduleId={moduleId}
        ctaLabel={ctaLabel}
        className={className}
      />
    );
  }
  if (kind === "process") {
    return <ProcessGuidance logId={logId} ctaLabel={ctaLabel} className={className} />;
  }
  return <QualityGuidance logId={logId} ctaLabel={ctaLabel} className={className} />;
}

// ── Module variant (supports streaming) ─────────────────────────────────────

function ModuleGuidance({
  logId,
  moduleId,
  ctaLabel,
  className,
}: {
  logId: string;
  moduleId: string;
  ctaLabel?: string;
  className?: string;
}) {
  const cached = useModuleGuidance(logId, moduleId);
  const regenerate = useRegenerateModuleGuidance(logId, moduleId);

  const [streamingText, setStreamingText] = useState<string | null>(null);
  const [streamFinal, setStreamFinal] = useState<GuidanceResponse | null>(null);
  const [streamError, setStreamError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const onClickGenerate = useCallback(async () => {
    setStreamError(null);
    setStreamFinal(null);
    setStreamingText("");
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    await streamModuleGuidance(
      logId,
      moduleId,
      {
        onDelta: (text) => setStreamingText((cur) => (cur ?? "") + text),
        onFinal: (resp) => {
          setStreamFinal(resp);
          setStreamingText(null);
        },
        onError: (msg) => {
          setStreamError(msg);
          setStreamingText(null);
        },
      },
      ctrl.signal,
    );
  }, [logId, moduleId]);

  const onClickRefresh = useCallback(async () => {
    setStreamError(null);
    setStreamFinal(null);
    try {
      await regenerate.mutateAsync();
    } catch {
      // Surfaced via the mutation state below.
    }
  }, [regenerate]);

  const response =
    streamFinal ?? regenerate.data ?? cached.data ?? null;
  const isStreaming = streamingText !== null;
  const isRegenerating = regenerate.isPending;
  const error = streamError ?? (regenerate.error as Error | null)?.message ?? null;

  return (
    <Shell
      className={className}
      title="AI insights"
      response={response}
      streamingText={streamingText}
      isStreaming={isStreaming}
      isRegenerating={isRegenerating}
      error={error}
      onGenerate={() => {
        void cached.refetch().then((res) => {
          // If we have a cached entry, use it; otherwise stream a fresh one.
          if (res.data) return;
          void onClickGenerate();
        });
      }}
      onRegenerate={onClickRefresh}
      ctaLabel={ctaLabel ?? "Get AI insights"}
    />
  );
}

// ── Process variant ────────────────────────────────────────────────────────

function ProcessGuidance({
  logId,
  ctaLabel,
  className,
}: {
  logId: string;
  ctaLabel?: string;
  className?: string;
}) {
  const cached = useProcessGuidance(logId);
  const regenerate = useRegenerateProcessGuidance(logId);
  const response = regenerate.data ?? cached.data ?? null;
  const error =
    (cached.error as Error | null)?.message ??
    (regenerate.error as Error | null)?.message ??
    null;

  return (
    <Shell
      className={className}
      title="Process overview"
      response={response}
      streamingText={null}
      isStreaming={cached.isFetching && !response}
      isRegenerating={regenerate.isPending}
      error={error}
      onGenerate={() => void cached.refetch()}
      onRegenerate={() => void regenerate.mutateAsync().catch(() => {})}
      ctaLabel={ctaLabel ?? "Generate AI overview"}
    />
  );
}

// ── Import-quality variant ─────────────────────────────────────────────────

function QualityGuidance({
  logId,
  ctaLabel,
  className,
}: {
  logId: string;
  ctaLabel?: string;
  className?: string;
}) {
  const cached = useImportQualityGuidance(logId);
  const response = cached.data ?? null;
  return (
    <Shell
      className={className}
      title="Data-quality check"
      response={response}
      streamingText={null}
      isStreaming={cached.isFetching && !response}
      isRegenerating={false}
      error={(cached.error as Error | null)?.message ?? null}
      onGenerate={() => void cached.refetch()}
      onRegenerate={() => void cached.refetch()}
      ctaLabel={ctaLabel ?? "Check data quality"}
    />
  );
}

// ── Presentation shell ─────────────────────────────────────────────────────

function Shell({
  title,
  response,
  streamingText,
  isStreaming,
  isRegenerating,
  error,
  onGenerate,
  onRegenerate,
  ctaLabel,
  className,
}: {
  title: string;
  response: GuidanceResponse | null;
  streamingText: string | null;
  isStreaming: boolean;
  isRegenerating: boolean;
  error: string | null;
  onGenerate: () => void;
  onRegenerate: () => void;
  ctaLabel: string;
  className?: string;
}) {
  const { data: aiConfig } = useAiConfig();
  const configured = Boolean(aiConfig?.selected_provider && aiConfig?.selected_model);

  if (aiConfig !== undefined && !configured) {
    return (
      <Card className={className}>
        <CardContent className="flex items-start gap-3">
          <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-amber-500/10 text-amber-600 dark:text-amber-400">
            <Sparkles className="h-3.5 w-3.5" />
          </div>
          <div className="min-w-0 flex-1 text-sm">
            <div className="font-medium">{title}</div>
            <p className="mt-0.5 text-xs text-muted-foreground">
              No AI model configured.{" "}
              <a href="/settings/ai" className="font-medium underline underline-offset-2">
                Settings → AI
              </a>{" "}
              to set one up.
            </p>
          </div>
        </CardContent>
      </Card>
    );
  }

  const showStreaming = isStreaming || (streamingText !== null && streamingText.length > 0);
  const body = response?.guidance ?? null;

  if (!response && !showStreaming && !error) {
    return (
      <Card className={className}>
        <CardContent className="flex items-center gap-3">
          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
            <Sparkles className="h-3.5 w-3.5" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="text-sm font-medium">{title}</div>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Interpret these results with the configured AI model.
            </p>
          </div>
          <Button size="sm" onClick={onGenerate} className="shrink-0 gap-1.5 cursor-pointer">
            <Sparkles className="h-3.5 w-3.5" />
            {ctaLabel}
          </Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className={className}>
      <CardContent className="space-y-3">
        <header className="flex items-center gap-2">
          <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
            <Sparkles className="h-3.5 w-3.5" />
          </div>
          <div className="min-w-0 flex-1 text-sm font-medium">{title}</div>
          {response?.cached && (
            <Badge variant="outline" className="h-5 gap-1 border-0 bg-muted px-1.5 text-[10px]">
              <CheckCircle2 className="h-3 w-3" />
              Cached
            </Badge>
          )}
          {response && (
            <Button
              size="sm"
              variant="ghost"
              onClick={onRegenerate}
              disabled={isRegenerating || isStreaming}
              aria-label="Regenerate"
              className="h-7 gap-1.5 cursor-pointer"
            >
              {isRegenerating ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <RefreshCw className="h-3.5 w-3.5" />
              )}
              Regenerate
            </Button>
          )}
        </header>

        {error && (
          <div className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            {error}
            <Button
              size="sm"
              variant="ghost"
              onClick={onRegenerate}
              className="mt-1 h-6 px-2 text-xs cursor-pointer"
            >
              Retry
            </Button>
          </div>
        )}

        {showStreaming && (
          <InterpretationProse text={streamingText ?? ""} streaming />
        )}

        {body && (
          <>
            {body.interpretation && (
              <InterpretationProse text={body.interpretation} streaming={false} />
            )}
            {body.recommended_actions.length > 0 && (
              <ActionsList items={body.recommended_actions} />
            )}
            {body.anomaly_flags.length > 0 && <FlagsList items={body.anomaly_flags} />}
            <Footer response={response!} />
          </>
        )}
      </CardContent>
    </Card>
  );
}

function InterpretationProse({ text, streaming }: { text: string; streaming: boolean }) {
  return (
    <p className="text-sm leading-relaxed text-foreground whitespace-pre-wrap break-words">
      {text}
      {streaming && (
        <span
          aria-hidden
          className="ml-0.5 inline-block h-[0.85em] w-0.5 translate-y-[2px] animate-pulse rounded-sm bg-current opacity-60"
        />
      )}
    </p>
  );
}

function ActionsList({ items }: { items: string[] }) {
  return (
    <div>
      <h4 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        Recommended actions
      </h4>
      <ul className="space-y-1 text-sm">
        {items.map((a, i) => (
          <li key={i} className="flex gap-2">
            <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />
            <span>{a}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

const FLAG_META: Record<
  GuidanceSeverity,
  { icon: LucideIcon; cls: string; label: string }
> = {
  info: {
    icon: Info,
    cls: "border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-400",
    label: "Info",
  },
  warning: {
    icon: TriangleAlert,
    cls: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400",
    label: "Warning",
  },
  critical: {
    icon: AlertTriangle,
    cls: "border-destructive/30 bg-destructive/10 text-destructive",
    label: "Critical",
  },
};

function FlagsList({ items }: { items: GuidanceFlag[] }) {
  return (
    <div>
      <h4 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        Flags
      </h4>
      <ul className="space-y-1.5">
        {items.map((f, i) => {
          const meta = FLAG_META[f.severity];
          const Icon = meta.icon;
          return (
            <li
              key={i}
              className={cn(
                "flex items-start gap-2 rounded-md border px-2.5 py-1.5 text-xs",
                meta.cls,
              )}
            >
              <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span className="min-w-0">{f.message}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function Footer({ response }: { response: GuidanceResponse }) {
  const when = response.generated_at
    ? new Date(response.generated_at * 1000).toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
    : null;
  return (
    <p className="text-[10px] text-muted-foreground">
      {response.provider && response.model
        ? `${response.provider} · ${response.model}`
        : "AI-generated"}
      {when ? ` · ${when}` : null} · AI can make mistakes; verify important details.
    </p>
  );
}
