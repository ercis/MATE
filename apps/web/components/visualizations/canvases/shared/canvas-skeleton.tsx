"use client";

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
