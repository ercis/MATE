"use client";

import Link from "next/link";
import { Activity, Gauge, GitBranch } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { DRILL_PARAMS, activityHref, modulePath } from "@/lib/dashboards/drill";

/** Context-menu target: an activity node + the viewport point that was clicked. */
export interface DfgNodeMenuTarget {
  /** Full activity name (DFG node id == raw activity name). */
  activityId: string;
  /** Display label (may be truncated by the node renderer). */
  label: string;
  /** Viewport (client) coordinates of the right-click. */
  x: number;
  y: number;
}

const MENU_WIDTH = 248;
const MENU_HEIGHT = 128;

/**
 * Right-click menu on a DFG activity node offering jumps into other views of
 * the same log: the canonical activity view, the performance module and the
 * variants tab. Entries are real links (loading.tsx fires, cmd-click works);
 * the activity travels via the shared drill vocabulary.
 *
 * Rendered `position: fixed` at the pointer, with a full-viewport backdrop
 * that swallows the next click (and right-click) to dismiss.
 */
export function DfgNodeMenu({
  target,
  logId,
  onClose,
}: {
  target: DfgNodeMenuTarget;
  logId: string;
  onClose: () => void;
}) {
  const activityParam = encodeURIComponent(target.activityId);
  const left = Math.max(8, Math.min(target.x, window.innerWidth - MENU_WIDTH - 8));
  const top = Math.max(8, Math.min(target.y, window.innerHeight - MENU_HEIGHT - 8));

  return (
    <>
      <div
        className="fixed inset-0 z-40"
        onClick={onClose}
        onContextMenu={(e) => {
          e.preventDefault();
          onClose();
        }}
      />
      <div
        role="menu"
        aria-label={`Actions for ${target.activityId}`}
        className="fixed z-50 overflow-hidden rounded-md border bg-popover p-1 text-popover-foreground shadow-md"
        style={{ left, top, width: MENU_WIDTH }}
      >
        <div
          className="truncate px-2 py-1.5 text-[11px] font-medium uppercase tracking-wider text-muted-foreground"
          title={target.activityId}
        >
          {target.label}
        </div>
        <MenuItem
          icon={Activity}
          label="Open activity view"
          href={activityHref(logId, target.activityId)}
          onClose={onClose}
        />
        <MenuItem
          icon={Gauge}
          label="View performance metrics"
          href={`${modulePath(logId, "performance")}?${DRILL_PARAMS.activity}=${activityParam}`}
          onClose={onClose}
        />
        <MenuItem
          icon={GitBranch}
          label="Show variants with this activity"
          href={`/processes/${logId}?tab=variants&${DRILL_PARAMS.activity}=${activityParam}`}
          onClose={onClose}
        />
      </div>
    </>
  );
}

function MenuItem({
  icon: Icon,
  label,
  href,
  onClose,
}: {
  icon: LucideIcon;
  label: string;
  href: string;
  onClose: () => void;
}) {
  return (
    <Link
      role="menuitem"
      href={href}
      className="flex w-full cursor-pointer items-center gap-2 rounded-sm px-2 py-1.5 text-left text-sm outline-none hover:bg-accent hover:text-accent-foreground"
      onClick={onClose}
    >
      <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
      <span className="min-w-0 truncate">{label}</span>
    </Link>
  );
}
