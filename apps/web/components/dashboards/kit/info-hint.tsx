"use client";

import type { ReactNode } from "react";
import { Info } from "lucide-react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/cn";

/**
 * Small ⓘ that reveals a plain-language explanation on hover/focus.
 *
 * Promoted out of `modules/performance/panel/` — it was the only module with a
 * per-metric explanation affordance, and every module needed one. Use it beside
 * any figure whose meaning isn't obvious from its label: a number nobody can
 * interpret is a number nobody trusts.
 *
 * The global TooltipProvider (`components/providers.tsx`) supplies the Radix
 * provider, so this works anywhere in the app without extra setup.
 */
export function InfoHint({
  label,
  children,
  className,
}: {
  /** Accessible name for the button, e.g. "What does throughput mean?" */
  label: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          aria-label={label}
          // Cards are click-to-select in edit mode and can sit inside a drag
          // handle; opening an explanation must never start either.
          onClick={(e) => e.stopPropagation()}
          onPointerDown={(e) => e.stopPropagation()}
          className={cn(
            "inline-flex shrink-0 cursor-help items-center justify-center rounded-full",
            "text-muted-foreground/70 transition-colors hover:text-foreground",
            "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
            className,
          )}
        >
          <Info className="h-3 w-3" aria-hidden="true" />
        </button>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs space-y-1 text-left leading-relaxed">
        {children}
      </TooltipContent>
    </Tooltip>
  );
}
