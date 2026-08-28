"use client";

import { ExternalLink } from "lucide-react";

import { cn } from "@/lib/cn";
import type { WidgetHelp } from "@/lib/dashboard-queries";

/**
 * Renders a widget's (or panel's) structured `help:` block.
 *
 * Three labelled sections rather than one paragraph, because they answer three
 * different questions and a reader usually wants only one of them:
 *
 *   what     — what am I looking at?
 *   read     — how do I interpret it? (which direction is good)
 *   computed — how was it derived, and what's excluded?
 *
 * `computed` is the one authors skip and users need most: a figure whose
 * definition is invisible ("median over completed cases only") gets quietly
 * misread. Only `what` is required.
 *
 * Shared by the dashboard card header and the module panel so the same widget
 * is explained identically in both places.
 */
export function WidgetHelpBody({
  help,
  /** Fallback when a module hasn't declared `help:` yet — the manifest's
   * one-line `description`. Better than an empty popover. */
  fallback,
  className,
}: {
  help?: WidgetHelp | null;
  fallback?: string | null;
  className?: string;
}) {
  if (!help) {
    if (!fallback) return null;
    return <p className={cn("text-xs leading-relaxed", className)}>{fallback}</p>;
  }

  return (
    <div className={cn("space-y-2 text-xs leading-relaxed", className)}>
      <p>{help.what}</p>
      {help.read && <HelpSection title="How to read it">{help.read}</HelpSection>}
      {help.computed && <HelpSection title="How it's computed">{help.computed}</HelpSection>}
      {help.docs_url && (
        <a
          href={help.docs_url}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 text-primary hover:underline"
          onClick={(e) => e.stopPropagation()}
        >
          Documentation
          <ExternalLink className="h-3 w-3" aria-hidden="true" />
        </a>
      )}
    </div>
  );
}

function HelpSection({ title, children }: { title: string; children: string }) {
  return (
    <div>
      <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
        {title}
      </p>
      <p className="mt-0.5">{children}</p>
    </div>
  );
}

/** Does this widget have anything to explain? Lets a caller hide the ⓘ
 * entirely rather than open an empty popover. */
export function hasHelp(help?: WidgetHelp | null, fallback?: string | null): boolean {
  return Boolean(help?.what || fallback);
}
