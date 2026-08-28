"use client";

import { Loader2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { useShallow } from "zustand/react/shallow";

import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/cn";
import {
  selectActiveJobs,
  selectCounts,
  useJobsStore,
  type LiveJob,
} from "@/lib/stores/jobs";
import { useCancelJob } from "@/lib/queries";
import { useUi } from "@/lib/stores/ui";

export function JobsDock() {
  const active = useJobsStore(useShallow(selectActiveJobs));
  const counts = useJobsStore(useShallow(selectCounts));
  const setOpen = useJobsStore((s) => s.setDrawerOpen);
  // Follow the sidebar's persistent layout footprint, not its hover-peek:
  // auto mode keeps a w-14 rail (peek overlays content), pinned takes w-56.
  const collapsed = !useUi((s) => s.sidebarPinned);
  const [hover, setHover] = useState(false);
  const cancel = useCancelJob();

  // 30 s grace: if there's nothing active but a job just finished, briefly
  // keep the dock visible. We track the last-finished timestamp and use a
  // CSS transition for the slide-out (§7.9.2).
  const [showTrailing, setShowTrailing] = useState(false);
  useEffect(() => {
    if (active.length > 0) {
      setShowTrailing(true);
      return;
    }
    if (counts.finished === 0) {
      setShowTrailing(false);
      return;
    }
    const handle = setTimeout(() => setShowTrailing(false), 30_000);
    return () => clearTimeout(handle);
  }, [active.length, counts.finished]);

  if (active.length === 0 && !showTrailing) return null;

  const foreground = active[0];

  return (
    <div
      className={cn(
        // Offset past the sidebar so the dock floats over the content area and
        // never overlaps the sidebar's footer controls (theme / jobs / profile).
        "pointer-events-auto fixed bottom-4 z-40 max-w-md transition-all duration-150 ease-out",
        collapsed ? "left-[4.5rem]" : "left-[15rem]",
        active.length > 0 ? "translate-y-0 opacity-100" : "translate-y-2 opacity-0",
      )}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      {hover && active.length > 0 ? (
        <ExpandedStack
          jobs={active.slice(0, 3)}
          onClick={() => setOpen(true)}
          onCancel={(id) => cancel.mutate(id)}
        />
      ) : (
        <Pill
          count={active.length}
          foreground={foreground}
          onClick={() => setOpen(true)}
        />
      )}
    </div>
  );
}

function Pill({
  count,
  foreground,
  onClick,
}: {
  count: number;
  foreground?: LiveJob;
  onClick: () => void;
}) {
  const pct = computePct(foreground);
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex max-w-md cursor-pointer items-center gap-3 rounded-full border border-white/10 [border-top-color:var(--glass-refraction-top)] bg-card/80 px-4 py-2 text-sm shadow-lg backdrop-blur-xl backdrop-saturate-150 transition-shadow hover:shadow-xl supports-[backdrop-filter]:bg-card/70"
      aria-label="Open jobs drawer"
    >
      <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground" />
      <span className="font-medium tabular-nums">
        {count} {count === 1 ? "job" : "jobs"}
      </span>
      {foreground && (
        <>
          <span className="truncate text-muted-foreground" style={{ maxWidth: 220 }}>
            · {foreground.title}
          </span>
          {pct !== null && (
            <span className="tabular-nums text-muted-foreground">· {pct}%</span>
          )}
        </>
      )}
    </button>
  );
}

function ExpandedStack({
  jobs,
  onClick,
  onCancel,
}: {
  jobs: LiveJob[];
  onClick: () => void;
  onCancel: (id: string) => void;
}) {
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === "Enter") onClick();
      }}
      className="flex w-[360px] cursor-pointer flex-col gap-2 rounded-xl border border-white/10 [border-top-color:var(--glass-refraction-top)] bg-card/80 p-2 shadow-xl backdrop-blur-xl backdrop-saturate-150 supports-[backdrop-filter]:bg-card/70"
      aria-label="Open jobs drawer"
    >
      {jobs.map((j) => {
        const pct = computePct(j);
        return (
          <div
            key={j.id}
            className="flex items-center gap-2 rounded-md px-2 py-1.5 hover:bg-accent/40"
          >
            <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground" />
            <div className="min-w-0 flex-1">
              <div className="truncate text-xs font-medium">{j.title}</div>
              <Progress
                value={pct ?? undefined}
                className={cn("h-1", pct === null && "animate-pulse")}
              />
            </div>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="h-6 w-6 cursor-pointer"
              aria-label="Cancel"
              onClick={(e) => {
                e.stopPropagation();
                onCancel(j.id);
              }}
            >
              <X className="h-3 w-3" />
            </Button>
          </div>
        );
      })}
    </div>
  );
}

function computePct(j?: LiveJob): number | null {
  if (!j) return null;
  if (!j.progress_total || j.progress_total <= 0) return null;
  return Math.min(100, Math.max(0, Math.floor((j.progress_current / j.progress_total) * 100)));
}
