"use client";

import Link from "next/link";
import { ChevronRight } from "lucide-react";

import { activityHref } from "@/lib/dashboards/drill";

export interface SequenceItem {
  /** Raw activity name — the link target (`activityHref` encodes it). */
  raw: string;
  /** Display label, after user renames. */
  label: string;
}

export function SequenceStrip({ logId, items }: { logId: string; items: SequenceItem[] }) {
  if (items.length === 0) {
    return (
      <p className="text-sm italic text-muted-foreground">
        No activities recorded for this variant.
      </p>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-1 rounded-lg border border-border/50 bg-muted/30 p-2.5">
      {items.map((item, i) => (
        <span key={`${item.raw}-${i}`} className="contents">
          <Link
            href={activityHref(logId, item.raw)}
            className="inline-flex items-center gap-1 rounded border border-border/50 bg-background/80 px-2 py-1 text-xs font-medium transition-colors hover:border-border hover:bg-background"
          >
            <span className="rounded-sm bg-muted px-1.5 py-0 text-[9px] font-semibold tabular-nums text-muted-foreground/70">
              {i + 1}
            </span>
            <span className="truncate underline-offset-2 hover:underline">{item.label}</span>
          </Link>
          {i < items.length - 1 && (
            <ChevronRight className="h-3 w-3 shrink-0 text-muted-foreground/40" />
          )}
        </span>
      ))}
    </div>
  );
}
