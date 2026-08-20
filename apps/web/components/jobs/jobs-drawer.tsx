"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { useVirtualizer } from "@tanstack/react-virtual";
import { Inbox, MoreHorizontal, Pause, Play, Eye, XCircle } from "lucide-react";
import { toast } from "sonner";
import { toastError } from "@/lib/toast";
import { useCancelAllJobs } from "@/lib/queries";

import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { EmptyState } from "@/components/empty-state";
import { JobRow } from "@/components/jobs/job-row";
import { JobGroupCard } from "@/components/jobs/job-group-card";
import { api } from "@/lib/api";
import {
  selectActiveJobs,
  selectJobGroups,
  useJobsStore,
  type JobGroup,
  type LiveJob,
} from "@/lib/stores/jobs";

type Filter = "all" | "running" | "queued" | "finished";

const ACTIVE_STATUS = new Set(["queued", "running", "paused"]);

/** A drawer unit is either a parent/child group or a standalone job. */
type Unit =
  | { kind: "group"; group: JobGroup }
  | { kind: "standalone"; job: LiveJob };

/** A virtualizable row: a whole import group (collapsible card) or a standalone job. */
type DisplayRow =
  | { kind: "group"; key: string; group: JobGroup; expanded: boolean }
  | { kind: "standalone"; key: string; job: LiveJob };

function unitJobs(u: Unit): LiveJob[] {
  return u.kind === "group" ? [u.group.parent, ...u.group.children] : [u.job];
}

function unitActive(u: Unit): boolean {
  return u.kind === "group"
    ? u.group.active
    : ACTIVE_STATUS.has(u.job.status);
}

function unitMatches(u: Unit, needle: string): boolean {
  const job = u.kind === "group" ? u.group.parent : u.job;
  return job.title.toLowerCase().includes(needle) || job.id.includes(needle);
}

export function JobsDrawer() {
  const open = useJobsStore((s) => s.drawerOpen);
  const setOpen = useJobsStore((s) => s.setDrawerOpen);
  const paused = useJobsStore((s) => s.paused);
  const finishedHidden = useJobsStore((s) => s.finishedHidden);
  const active = useJobsStore(useShallow(selectActiveJobs));
  // Subscribe to the stable `byId` map ref (changes only on a real store
  // update) and derive groups via useMemo. selectJobGroups allocates fresh
  // wrapper objects, so using it as a useShallow selector would loop renders.
  const byId = useJobsStore((s) => s.byId);
  const { groups, standalone } = useMemo(() => selectJobGroups(byId), [byId]);
  const setFinishedHidden = useJobsStore((s) => s.setFinishedHidden);
  const cancelAll = useCancelAllJobs();
  const [filter, setFilter] = useState<Filter>("running");
  const [q, setQ] = useState("");
  // Group ids the user has collapsed; groups are expanded by default.
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const hasActive = active.length > 0;
  const toggleGroup = (id: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  // `j j` chord opens the drawer.
  useEffect(() => {
    let lastJ = 0;
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLElement) {
        const tag = e.target.tagName;
        if (tag === "INPUT" || tag === "TEXTAREA" || e.target.isContentEditable) return;
      }
      if (e.key.toLowerCase() === "j") {
        const now = Date.now();
        if (now - lastJ < 700) {
          setOpen(true);
          lastJ = 0;
        } else {
          lastJ = now;
        }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [setOpen]);

  const ordered = useMemo<DisplayRow[]>(() => {
    const units: Unit[] = [
      ...groups.map((group): Unit => ({ kind: "group", group })),
      ...standalone.map((job): Unit => ({ kind: "standalone", job })),
    ];

    // Active units first (matching the old `[...active, ...finished]` order),
    // each block already creation-sorted by the selectors.
    const activeUnits = units.filter(unitActive);
    const finishedUnits = units.filter((u) => !unitActive(u));

    let visible: Unit[];
    if (filter === "running") {
      visible = activeUnits.filter((u) =>
        unitJobs(u).some((j) => j.status === "running"),
      );
    } else if (filter === "queued") {
      visible = activeUnits.filter((u) =>
        unitJobs(u).some((j) => j.status === "queued" || j.status === "paused"),
      );
    } else if (filter === "finished") {
      visible = finishedHidden ? [] : finishedUnits;
    } else {
      // "all": active always shown; finished honor the hidden toggle.
      visible = finishedHidden ? activeUnits : [...activeUnits, ...finishedUnits];
    }

    if (q) {
      const needle = q.toLowerCase();
      visible = visible.filter((u) => unitMatches(u, needle));
    }

    // Flatten units → display rows. A group is one collapsible card that nests
    // its child step rows internally, so it stays a single virtualized row.
    const rows: DisplayRow[] = [];
    for (const u of visible) {
      if (u.kind === "standalone") {
        rows.push({ kind: "standalone", key: u.job.id, job: u.job });
        continue;
      }
      rows.push({
        kind: "group",
        key: u.group.parent.id,
        group: u.group,
        expanded: !collapsed.has(u.group.parent.id),
      });
    }
    return rows;
  }, [groups, standalone, filter, finishedHidden, q, collapsed]);


  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetContent side="left" className="flex w-[420px] flex-col gap-0 p-0 sm:max-w-[420px]">
        <SheetHeader className="space-y-3 border-b border-border px-4 py-3">
          <div className="flex items-center justify-between pr-8">
            <SheetTitle>Jobs</SheetTitle>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon" className="cursor-pointer h-8 w-8" aria-label="Queue actions">
                  <MoreHorizontal className="h-4 w-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                {paused ? (
                  <DropdownMenuItem
                    onSelect={async () => {
                      try {
                        await api("/api/v1/jobs/queue/resume", { method: "POST" });
                      } catch (e) {
                        toastError(`Resume failed: ${(e as Error).message}`);
                      }
                    }}
                    className="cursor-pointer"
                  >
                    <Play className="mr-2 h-3.5 w-3.5" /> Resume queue
                  </DropdownMenuItem>
                ) : (
                  <DropdownMenuItem
                    onSelect={async () => {
                      try {
                        await api("/api/v1/jobs/queue/pause", { method: "POST" });
                      } catch (e) {
                        toastError(`Pause failed: ${(e as Error).message}`);
                      }
                    }}
                    className="cursor-pointer"
                  >
                    <Pause className="mr-2 h-3.5 w-3.5" /> Pause queue
                  </DropdownMenuItem>
                )}
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  disabled={!hasActive || cancelAll.isPending}
                  onSelect={(e) => {
                    e.preventDefault();
                    if (!hasActive || cancelAll.isPending) return;
                    cancelAll.mutate(undefined, {
                      onSuccess: (data) => {
                        toast.success(
                          data.cancelled === 0
                            ? "No active jobs to cancel"
                            : `Cancelled ${data.cancelled} job${data.cancelled === 1 ? "" : "s"}`,
                        );
                      },
                      onError: (e) => toastError(`Cancel all failed: ${(e as Error).message}`),
                    });
                  }}
                  className="cursor-pointer text-destructive focus:text-destructive"
                >
                  <XCircle className="mr-2 h-3.5 w-3.5" /> Cancel all running
                </DropdownMenuItem>
                {finishedHidden && (
                  <>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem
                      onSelect={() => setFinishedHidden(false)}
                      className="cursor-pointer"
                    >
                      <Eye className="mr-2 h-3.5 w-3.5" /> Show finished
                    </DropdownMenuItem>
                  </>
                )}
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
          {paused && <Badge className="border-0 bg-chart-4/20 text-foreground w-fit">Paused</Badge>}
          <Tabs value={filter} onValueChange={(v) => setFilter(v as Filter)}>
            <TabsList className="grid w-full grid-cols-4">
              <TabsTrigger value="all" className="cursor-pointer text-xs">All</TabsTrigger>
              <TabsTrigger value="running" className="cursor-pointer text-xs">Running</TabsTrigger>
              <TabsTrigger value="queued" className="cursor-pointer text-xs">Queued</TabsTrigger>
              <TabsTrigger value="finished" className="cursor-pointer text-xs">Finished</TabsTrigger>
            </TabsList>
          </Tabs>
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Filter by title or job id"
            className="h-8 text-xs"
          />
        </SheetHeader>

        <DrawerBody rows={ordered} onToggleGroup={toggleGroup} />
      </SheetContent>
    </Sheet>
  );
}

function DrawerBody({
  rows,
  onToggleGroup,
}: {
  rows: DisplayRow[];
  onToggleGroup: (id: string) => void;
}) {
  const parentRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => parentRef.current,
    // Standalone rows carry a details affordance and run tall; a collapsed group
    // is a compact header, an expanded one grows by its nested child rows.
    // measureElement corrects the estimate.
    estimateSize: (i) => {
      const row = rows[i];
      if (row.kind === "standalone") return 200;
      return row.expanded ? 76 + row.group.children.length * 44 : 76;
    },
    overscan: 6,
  });

  if (rows.length === 0) {
    return (
      <EmptyState
        icon={Inbox}
        title="No jobs to show"
        description="Imports, module computes, and other long-running operations show up here."
        className="m-0 px-6"
      />
    );
  }

  const items = virtualizer.getVirtualItems();
  return (
    <div ref={parentRef} className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
      <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
        {items.map((v) => {
          const row = rows[v.index];
          return (
            <div
              key={row.key}
              ref={virtualizer.measureElement}
              data-index={v.index}
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                width: "100%",
                transform: `translateY(${v.start}px)`,
                paddingBottom: 8,
              }}
            >
              {row.kind === "group" ? (
                <JobGroupCard
                  group={row.group}
                  expanded={row.expanded}
                  onToggle={() => onToggleGroup(row.group.parent.id)}
                />
              ) : (
                <JobRow job={row.job} />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
