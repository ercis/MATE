"use client";

import type { ReactNode } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/cn";

/**
 * The loading / empty / error contract every dashboard card body goes through.
 *
 * Also the card's SINGLE SCROLL OWNER. The card frame itself is
 * `overflow-hidden`, so if a widget adds its own scrolling container the board
 * ends up with a scrollbar inside a scrollbar inside the page — which is what
 * a too-small card used to produce. Let this own it: pass `scroll` only for
 * genuinely tabular content, and otherwise size the widget to the card.
 */
export function CardShell({
  loading,
  empty,
  error,
  emptyText = "No data for this log yet.",
  errorText = "Could not load this card.",
  scroll = false,
  className,
  children,
}: {
  loading?: boolean;
  empty?: boolean;
  /** Truthy renders the error state — pass the caught error or just `true`. */
  error?: unknown;
  emptyText?: string;
  errorText?: string;
  /** Opt in to vertical scrolling for tabular content. Default is to fit. */
  scroll?: boolean;
  className?: string;
  children: ReactNode;
}) {
  if (loading) return <Skeleton className="h-full min-h-24 w-full" />;
  if (error) return <CardError>{errorText}</CardError>;
  if (empty) return <CardEmpty>{emptyText}</CardEmpty>;
  return (
    <div className={cn("h-full min-h-0", scroll ? "overflow-y-auto" : "overflow-hidden", className)}>
      {children}
    </div>
  );
}

/** "Nothing to show" — a real state, not a failure. */
export function CardEmpty({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-full min-h-24 items-center justify-center px-3 text-center text-xs text-muted-foreground">
      {children}
    </div>
  );
}

/** Something went wrong fetching or rendering. Deliberately quiet: one failing
 * card must not shout over the eleven that worked. */
export function CardError({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-full min-h-24 items-center justify-center px-3 text-center text-xs text-muted-foreground">
      {children}
    </div>
  );
}

/** A titled block inside a card that shows more than one thing. Use sparingly —
 * a card needing several sections is usually two cards. */
export function CardSection({
  title,
  action,
  className,
  children,
}: {
  title?: string;
  action?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section className={cn("flex min-h-0 flex-col", className)}>
      {(title || action) && (
        <header className="mb-1.5 flex shrink-0 items-center justify-between gap-2">
          {title && (
            <h3 className="truncate text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
              {title}
            </h3>
          )}
          {action}
        </header>
      )}
      <div className="min-h-0 flex-1">{children}</div>
    </section>
  );
}
