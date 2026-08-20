"use client";

import { useState } from "react";
import { ArrowRight, Check, Compass, Lock, SlidersHorizontal } from "lucide-react";

import { cn } from "@/lib/cn";

export interface NavTarget {
  id: string;
  label: string;
  kind: string;
  href: string;
  requires_log: boolean;
  available: boolean;
}

export interface ActionTarget {
  setting: string;
  value: string | boolean;
  label: string;
  target: string;
}

const chipClass = cn(
  "group flex w-full cursor-pointer items-center gap-2.5 rounded-lg border border-sidebar-border bg-sidebar-accent/40 px-3 py-2 text-left transition-colors",
  "hover:border-sidebar-primary/40 hover:bg-sidebar-accent/70",
);

/**
 * Renders the navigation + settings suggestions returned by `/api/v1/ai/route`
 * as clickable chips below an assistant message. Both are purely additive – the
 * user can always ignore them, and settings only change on click.
 */
export function NavWidget({
  targets = [],
  actions = [],
  onNavigate,
  onAction,
}: {
  targets?: NavTarget[];
  actions?: ActionTarget[];
  onNavigate: (target: NavTarget) => void;
  onAction?: (action: ActionTarget) => void;
}) {
  const [applied, setApplied] = useState<Set<string>>(new Set());

  if (targets.length === 0 && actions.length === 0) return null;

  const apply = (a: ActionTarget) => {
    if (applied.has(a.setting)) return;
    onAction?.(a);
    setApplied((prev) => new Set(prev).add(a.setting));
  };

  return (
    <div className="mt-2 space-y-2.5">
      {targets.length > 0 && (
        <div className="space-y-1.5">
          <div className="flex items-center gap-1.5 px-0.5 text-[10px] font-medium uppercase tracking-wide text-sidebar-foreground/45">
            <Compass className="h-3 w-3" />
            Jump to
          </div>
          {targets.map((t) => (
            <button key={t.id} type="button" onClick={() => onNavigate(t)} className={chipClass}>
              <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-sidebar text-sidebar-primary">
                {t.requires_log && !t.available ? (
                  <Lock className="h-3 w-3" />
                ) : (
                  <ArrowRight className="h-3 w-3" />
                )}
              </div>
              <div className="min-w-0 flex-1">
                <div className="truncate text-xs font-medium text-sidebar-foreground">
                  {t.label}
                </div>
                {t.requires_log && !t.available && (
                  <div className="mt-0.5 text-[10px] text-sidebar-foreground/50">
                    Open a process to view this module&apos;s panel – opens settings for now.
                  </div>
                )}
              </div>
            </button>
          ))}
        </div>
      )}

      {actions.length > 0 && (
        <div className="space-y-1.5">
          <div className="flex items-center gap-1.5 px-0.5 text-[10px] font-medium uppercase tracking-wide text-sidebar-foreground/45">
            <SlidersHorizontal className="h-3 w-3" />
            Apply setting
          </div>
          {actions.map((a) => {
            const done = applied.has(a.setting);
            return (
              <button
                key={a.setting}
                type="button"
                disabled={done}
                onClick={() => apply(a)}
                className={cn(chipClass, done && "cursor-default opacity-70 hover:bg-sidebar-accent/40")}
              >
                <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-sidebar text-sidebar-primary">
                  {done ? <Check className="h-3 w-3" /> : <SlidersHorizontal className="h-3 w-3" />}
                </div>
                <div className="min-w-0 flex-1 truncate text-xs font-medium text-sidebar-foreground">
                  {done ? `${a.label} – applied` : a.label}
                </div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
