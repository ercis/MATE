"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTheme } from "next-themes";
import { useState } from "react";
import {
  Activity,
  Cog,
  FileBox,
  FolderKanban,
  LayoutDashboard,
  Moon,
  Pin,
  PinOff,
  ShieldCheck,
  Sun,
} from "lucide-react";
import { useShallow } from "zustand/react/shallow";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";

import { cn } from "@/lib/cn";
import { prefetchDashboards, prefetchEventLogs, prefetchModules } from "@/lib/client-prefetch";
import { useMounted } from "@/lib/use-mounted";
import { useUi } from "@/lib/stores/ui";
import { Button } from "@/components/ui/button";
import { MateLogo } from "@/components/mate-logo";
import { UserMenu } from "@/components/user-menu";
import { useTrack } from "@/lib/analytics/hooks";
import { EV } from "@/lib/analytics/events";
import { selectCounts, useJobsStore } from "@/lib/stores/jobs";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

interface NavItem {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  match: (pathname: string) => boolean;
  /** Warm this section's list query on hover/focus so the click is instant. */
  prefetch?: (qc: QueryClient) => void;
}

const NAV: NavItem[] = [
  {
    href: "/processes",
    label: "Processes",
    icon: FolderKanban,
    match: (p) => p === "/" || p.startsWith("/processes"),
    prefetch: (qc) => prefetchEventLogs(qc),
  },
  {
    href: "/dashboards",
    label: "Dashboards",
    icon: LayoutDashboard,
    match: (p) => p.startsWith("/dashboards"),
    prefetch: (qc) => prefetchDashboards(qc),
  },
  {
    href: "/modules",
    label: "Module Settings",
    icon: FileBox,
    match: (p) => p.startsWith("/modules"),
    prefetch: (qc) => prefetchModules(qc),
  },
  {
    // Deep-link straight to the default tab – `/settings` is a server
    // redirect, which would cost a second serial RSC roundtrip per click.
    href: "/settings/general",
    label: "Settings",
    icon: Cog,
    match: (p) => p.startsWith("/settings"),
  },
];

// Admin-only entries, appended to NAV when the session user has the `admin`
// realm role. The page + API independently enforce the role server-side.
const ADMIN_NAV: NavItem[] = [
  {
    href: "/admin/overview",
    label: "Admin",
    icon: ShieldCheck,
    match: (p) => p.startsWith("/admin"),
  },
];

export function Sidebar({ isAdmin = false }: { isAdmin?: boolean }) {
  const pinned = useUi((s) => s.sidebarPinned);
  const togglePinned = useUi((s) => s.toggleSidebarPinned);
  // Transient hover-peek: ephemeral (never persisted). In auto mode the panel
  // expands while the cursor is over it and retracts when it leaves.
  const [hovered, setHovered] = useState(false);
  const pathname = usePathname();
  const track = useTrack();
  const qc = useQueryClient();

  const expanded = pinned || hovered;
  const collapsed = !expanded;

  const onTogglePin = () => {
    track(EV.SIDEBAR_TOGGLED, { pinned_after: !pinned });
    togglePinned();
  };

  return (
    // Outer rail reserves the layout footprint: w-56 when pinned (pushes the
    // content area), w-14 in auto mode (the panel below overlays content on
    // hover instead, so peeking never reflows the app).
    <div className={cn("relative shrink-0", pinned ? "w-56" : "w-14")}>
      <aside
        onMouseLeave={() => setHovered(false)}
        className={cn(
          "flex h-screen flex-col border-r border-white/10 [border-top-color:var(--glass-refraction-top)] bg-sidebar/85 text-sidebar-foreground backdrop-blur-xl backdrop-saturate-150 transition-[width] duration-150 ease-out supports-[backdrop-filter]:bg-sidebar/70",
          // Auto mode → float over content as an elevated overlay; pinned →
          // static, taking its place in the flex flow.
          !pinned && "absolute inset-y-0 left-0 z-30 shadow-xl shadow-black/20",
          expanded ? "w-56" : "w-14",
        )}
        aria-label="Primary navigation"
      >
      {/* Logo stays pinned top-left in both states: same left padding whether
          collapsed or expanded, so it never re-centers/"flies in" on the
          width transition when the sidebar auto-collapses. */}
      <div className="flex items-center gap-2 px-3 py-3.5">
        {/* Collapsed rail shows just the brand mark; the pin toggle lives in the
            expanded header (auto-mode expands on hover, so the collapsed top is
            branding, not a control). */}
        <MateLogo animateOnHover className="h-7 w-7 shrink-0 text-sidebar-foreground" />
        {!collapsed && (
          <>
            <span className="truncate text-sm font-semibold tracking-tight">PM-MATE</span>
            <button
              type="button"
              onClick={onTogglePin}
              className="ml-auto cursor-pointer rounded-md p-1.5 text-sidebar-foreground/60 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
              aria-label={pinned ? "Unpin sidebar (auto-hide)" : "Pin sidebar open"}
              aria-pressed={pinned}
              title={pinned ? "Unpin sidebar (auto-hide)" : "Pin sidebar open"}
            >
              {pinned ? <PinOff className="h-4 w-4" /> : <Pin className="h-4 w-4" />}
            </button>
          </>
        )}
      </div>

      <nav className="flex-1 px-2 pt-1" onMouseEnter={() => setHovered(true)}>
        <ul className="space-y-0.5">
          {(isAdmin ? [...NAV, ...ADMIN_NAV] : NAV).map((item) => {
            const Icon = item.icon;
            const active = item.match(pathname);
            const link = (
              <Link
                href={item.href}
                // Full prefetch (RSC payload incl. the page, not just the
                // loading boundary) – top-level sections are client shells, so
                // the payload is tiny and the first click commits instantly.
                prefetch={true}
                data-tour={item.href === "/processes" ? "nav-processes" : undefined}
                aria-current={active ? "page" : undefined}
                onMouseEnter={() => item.prefetch?.(qc)}
                onFocus={() => item.prefetch?.(qc)}
                className={cn(
                  // overflow-hidden clips the label while the sidebar is
                  // mid-width-transition so a long name ("Module Settings")
                  // never wraps to a 2nd line before the panel reaches w-56.
                  "flex h-9 items-center gap-3 overflow-hidden rounded-md px-3 text-sm transition-colors cursor-pointer",
                  active
                    ? "bg-sidebar-accent text-sidebar-accent-foreground"
                    : "text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
                )}
              >
                <Icon className="h-4 w-4 shrink-0" />
                {!collapsed && <span className="whitespace-nowrap">{item.label}</span>}
              </Link>
            );
            return (
              <li key={item.href}>
                {collapsed ? (
                  <Tooltip>
                    <TooltipTrigger asChild>{link}</TooltipTrigger>
                    <TooltipContent side="right">{item.label}</TooltipContent>
                  </Tooltip>
                ) : (
                  link
                )}
              </li>
            );
          })}
        </ul>
      </nav>

      <div
        className={cn(
          "flex items-center gap-2 border-t border-sidebar-border px-3 py-2",
          collapsed && "flex-col gap-1 px-1",
        )}
      >
        <ThemeToggle collapsed={collapsed} />
        <JobsSidebarButton collapsed={collapsed} />
        <UserMenu collapsed={collapsed} />
      </div>
      {!collapsed && (
        <div className="border-t border-sidebar-border px-4 py-2 text-[10px] uppercase tracking-wide text-sidebar-foreground/40">
          v0.1.1
        </div>
      )}
      </aside>
    </div>
  );
}

function JobsSidebarButton({ collapsed }: { collapsed: boolean }) {
  const counts = useJobsStore(useShallow(selectCounts));
  const setOpen = useJobsStore((s) => s.setDrawerOpen);
  const active = counts.running + counts.queued;
  const running = counts.running;

  const button = (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      aria-label={active ? `${active} active jobs` : "Open jobs drawer"}
      onClick={() => setOpen(true)}
      className={cn(
        "relative h-8 w-8 cursor-pointer text-sidebar-foreground/70",
        active > 0 && "text-sidebar-foreground",
      )}
    >
      <Activity className={cn("h-4 w-4", running > 0 && "animate-heartbeat")} />
      {active > 0 && (
        <span
          aria-hidden
          className="absolute -right-0.5 -top-0.5 flex h-3.5 min-w-3.5 items-center justify-center rounded-full bg-primary px-1 text-[9px] font-medium leading-none tabular-nums text-primary-foreground"
        >
          {active > 9 ? "9+" : active}
        </span>
      )}
    </Button>
  );

  return (
    <Tooltip>
      <TooltipTrigger asChild>{button}</TooltipTrigger>
      <TooltipContent side={collapsed ? "right" : "top"}>
        {active ? `${active} active job${active === 1 ? "" : "s"}` : "Jobs"}
      </TooltipContent>
    </Tooltip>
  );
}

function ThemeToggle({ collapsed }: { collapsed: boolean }) {
  const { resolvedTheme, setTheme } = useTheme();
  // resolvedTheme is client-only; gate on mount so SSR and first client render
  // agree on the icon/label (otherwise React hydration mismatch #418).
  const mounted = useMounted();
  const isDark = mounted && resolvedTheme === "dark";
  const Icon = isDark ? Moon : Sun;
  const button = (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      aria-label={
        mounted ? (isDark ? "Switch to light mode" : "Switch to dark mode") : "Toggle theme"
      }
      onClick={() => setTheme(isDark ? "light" : "dark")}
      className="h-8 w-8 cursor-pointer text-sidebar-foreground/70"
    >
      <Icon className="h-4 w-4" />
    </Button>
  );
  return (
    <Tooltip>
      <TooltipTrigger asChild>{button}</TooltipTrigger>
      <TooltipContent side={collapsed ? "right" : "top"}>
        {mounted ? (isDark ? "Light mode" : "Dark mode") : "Theme"}
      </TooltipContent>
    </Tooltip>
  );
}
