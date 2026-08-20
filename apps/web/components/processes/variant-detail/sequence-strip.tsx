"use client";

import { ChevronRight } from "lucide-react";

export function SequenceStrip({ activities }: { activities: string[] }) {
  if (activities.length === 0) {
    return (
      <p className="text-sm italic text-muted-foreground">
        No activities recorded for this variant.
      </p>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-1 rounded-lg border border-border/50 bg-muted/30 p-2.5">
      {activities.map((activity, i) => (
        <span key={`${activity}-${i}`} className="contents">
          <span className="inline-flex items-center gap-1 rounded px-2 py-1 text-xs font-medium bg-background/80 border border-border/50">
            <span className="rounded-sm bg-muted px-1.5 py-0 text-[9px] tabular-nums font-semibold text-muted-foreground/70">
              {i + 1}
            </span>
            <span className="truncate">{activity}</span>
          </span>
          {i < activities.length - 1 && (
            <ChevronRight className="h-3 w-3 shrink-0 text-muted-foreground/40" />
          )}
        </span>
      ))}
    </div>
  );
}
