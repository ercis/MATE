"use client";

import { Tabs as TabsPrimitive } from "radix-ui";
import { motion, useReducedMotion } from "framer-motion";

import { cn } from "@/lib/cn";
import { formatNumber } from "@/lib/format";

export interface ProcessTabItem {
  value: string;
  label: string;
  /** Live count rendered as a subtle badge; omit/null to hide (e.g. while the
   *  log is still importing). */
  count?: number | null;
  disabled?: boolean;
}

// One shared layout id so the active-tab underline slides between triggers
// (framer-motion "magic move") instead of jumping — motion as state feedback.
const UNDERLINE_LAYOUT_ID = "process-tab-underline";

/**
 * Section navigation for the process detail view: underline tabs with a sliding
 * active indicator, scoped here (not the global `Tabs`) so it reads as primary
 * nav, distinct from the segmented filter controls elsewhere. Renders Radix
 * `Tabs.Trigger`s, so it must live inside a `<Tabs>` root.
 */
export function ProcessTabs({
  items,
  value,
  className,
}: {
  items: ProcessTabItem[];
  value: string;
  className?: string;
}) {
  const reduceMotion = useReducedMotion();
  return (
    <TabsPrimitive.List
      className={cn(
        // Scrolls horizontally when the tabs overflow (narrow screens), with the
        // scrollbar hidden so the bar stays clean.
        "relative flex min-w-0 items-stretch gap-0.5 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden",
        className,
      )}
    >
      {items.map((item) => {
        const active = item.value === value;
        const hasCount = typeof item.count === "number";
        return (
          <TabsPrimitive.Trigger
            key={item.value}
            value={item.value}
            disabled={item.disabled}
            className={cn(
              "group relative inline-flex shrink-0 cursor-pointer items-center gap-1.5 whitespace-nowrap rounded-t-md px-3 py-3 text-sm font-medium outline-none transition-colors",
              "text-muted-foreground hover:text-foreground hover:bg-muted/50",
              "focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:ring-inset",
              "disabled:pointer-events-none disabled:opacity-40",
              "data-[state=active]:text-foreground",
            )}
          >
            <span>{item.label}</span>
            {hasCount && (
              <span
                className={cn(
                  "rounded px-1 py-px text-[10px] font-medium tabular-nums transition-colors",
                  active
                    ? "bg-primary/10 text-primary"
                    : "bg-muted text-muted-foreground/80 group-hover:text-foreground/70",
                )}
              >
                {formatNumber(item.count as number)}
              </span>
            )}
            {active && (
              <motion.span
                layoutId={UNDERLINE_LAYOUT_ID}
                className="pointer-events-none absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-primary"
                transition={
                  reduceMotion
                    ? { duration: 0 }
                    : { type: "tween", duration: 0.2, ease: [0.22, 1, 0.36, 1] }
                }
              />
            )}
          </TabsPrimitive.Trigger>
        );
      })}
    </TabsPrimitive.List>
  );
}
