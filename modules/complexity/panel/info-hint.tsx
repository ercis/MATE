"use client";

import type { ReactNode } from "react";
import { Info } from "lucide-react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/cn";

/**
 * Small info (ⓘ) button that reveals a plain-language explanation on
 * hover/focus. Own copy of the performance module's `panel/info-hint.tsx`
 * (module panels bundle separately, so no cross-module imports). The global
 * TooltipProvider (apps/web/components/providers.tsx) supplies the Radix
 * provider.
 */
export function InfoHint({
  label,
  children,
  className,
}: {
  /** Accessible name for the button, e.g. "What does variant entropy mean?" */
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
          onClick={(e) => e.stopPropagation()}
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
