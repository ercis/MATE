"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, FileText, Sparkles } from "lucide-react";

import { Badge } from "@/components/ui/badge";

import type { CdeStoredExplanation } from "./queries";

export function ExplanationView({
  stored,
}: {
  stored: CdeStoredExplanation | undefined;
}) {
  if (!stored) {
    return (
      <p className="rounded border border-dashed py-6 text-center text-xs text-muted-foreground">
        Select a drift and click <strong>Run analysis</strong> to generate an
        explanation.
      </p>
    );
  }
  const explanation = stored.explanation;

  return (
    <div className="space-y-4">
      <section className="rounded-md border bg-muted/30 p-4">
        <header className="mb-2 flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
          <Sparkles className="h-3.5 w-3.5" />
          Executive summary
        </header>
        <p className="whitespace-pre-wrap text-sm leading-relaxed">
          {explanation.summary || (
            <em className="text-muted-foreground">No summary generated.</em>
          )}
        </p>
      </section>

      {explanation.ranked_causes.length === 0 ? (
        <p className="rounded border border-dashed py-6 text-center text-xs text-muted-foreground">
          No causes were ranked above the configured confidence threshold.
        </p>
      ) : (
        <ol className="space-y-3">
          {explanation.ranked_causes.map((cause, i) => (
            <CauseCard key={`${cause.source_document}-${i}`} cause={cause} rank={i + 1} />
          ))}
        </ol>
      )}
    </div>
  );
}

function CauseCard({
  cause,
  rank,
}: {
  cause: import("./queries").CdeRankedCause;
  rank: number;
}) {
  const [open, setOpen] = useState(rank === 1);
  const pct = Math.round(cause.confidence_score * 100);

  return (
    <li className="rounded-md border">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-start gap-2 px-3 py-2 text-left"
      >
        {open ? (
          <ChevronDown className="mt-0.5 h-3.5 w-3.5 text-muted-foreground" />
        ) : (
          <ChevronRight className="mt-0.5 h-3.5 w-3.5 text-muted-foreground" />
        )}
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-baseline gap-2">
            <span className="text-[11px] font-semibold tabular-nums text-muted-foreground">
              #{rank}
            </span>
            <Badge variant="outline" className="text-[10px]">
              {cause.context_category || "Uncategorised"}
            </Badge>
            <span className="ml-auto text-xs tabular-nums">{pct}%</span>
          </div>
          <p className="mt-1 text-sm leading-relaxed">
            {cause.cause_description}
          </p>
        </div>
      </button>

      {open && (
        <div className="space-y-2 border-t bg-muted/20 px-6 py-3 text-xs">
          <div className="flex items-center gap-1.5 font-mono text-muted-foreground">
            <FileText className="h-3 w-3" />
            {cause.source_document}
          </div>
          <blockquote className="border-l-2 pl-3 italic text-muted-foreground">
            {cause.evidence_snippet}
          </blockquote>
          <div className="h-1.5 w-full overflow-hidden rounded bg-muted">
            <div
              className="h-full bg-primary transition-all"
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>
      )}
    </li>
  );
}
