"use client";

import { Loader2 } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";

/**
 * Placeholder shown while ELK is computing the layout. Same outer shape as
 * the real `CanvasShell` so the canvas area doesn't visibly jump when the
 * layout finishes.
 */
export function CanvasLayoutSkeleton() {
  return (
    <div className="h-[640px] w-full overflow-hidden rounded-xl border bg-card p-4">
      <Skeleton className="h-full w-full" />
    </div>
  );
}

/**
 * Named loading state for a canvas that is being *derived* rather than fetched.
 *
 * A bare skeleton over a mined process model reads as "something is missing
 * here" - users assumed a diagram had to be uploaded before it would appear.
 * This says what is being computed and that waiting is the whole job.
 *
 * Lives in `apps/web` (and is listed in `runtime-externals.json`) so module
 * panels can use it: Tailwind never scans `modules/**`, so a state built there
 * would silently lose its arbitrary utilities.
 */
export function CanvasComputingState({
  title,
  description,
}: {
  title: string;
  description?: string;
}) {
  return (
    <div className="relative h-[640px] w-full overflow-hidden rounded-xl border bg-card p-4">
      <Skeleton className="h-full w-full" />
      <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 px-6 text-center">
        <div className="flex items-center gap-2 text-sm font-medium text-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          {title}
        </div>
        {description && (
          <p className="max-w-md text-xs leading-relaxed text-muted-foreground">{description}</p>
        )}
      </div>
    </div>
  );
}
