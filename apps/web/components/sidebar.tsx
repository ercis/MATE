"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTheme } from "next-themes";
import {
  Activity,
  Cog,
  FileBox,
  FolderKanban,
  LayoutDashboard,
  Moon,
  PanelLeftClose,
  Pickaxe,
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
    label: "Modules",
    icon: FileBox,
    match: (p) => p.startsWith("/modules"),
    prefetch: (qc) => prefetchModules(qc),
  },
  {
    href: "/settings",
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
  const collapsed = useUi((s) => s.sidebarCollapsed);
  const toggle = useUi((s) => s.toggleSidebar);
  const pathname = usePathname();
  const track = useTrack();
  const qc = useQueryClient();
  const onToggle = () => {
    track(EV.SIDEBAR_TOGGLED, { collapsed_after: !collapsed });
    toggle();
  };

  return (
    <aside
      className={cn(
        "flex h-screen flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground transition-[width] duration-150 ease-out",
        collapsed ? "w-14" : "w-56",
      )}
      aria-label="Primary navigation"
    >
      <div
        className={cn(
          "flex items-center py-3.5",
          collapsed ? "justify-center px-2" : "gap-2 px-3",
        )}
      >
        {!collapsed && (
          <>
            <div
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-sidebar-primary text-sidebar-primary-foreground dark:bg-sidebar-foreground dark:text-sidebar"
              aria-hidden
            >
              <Pickaxe className="h-4 w-4" />
            </div>
            <span className="truncate text-sm font-semibold tracking-tight">MATE Hub</span>
          </>
        )}
        <button
          type="button"
          onClick={onToggle}
          className={cn(
            "cursor-pointer rounded-md p-1.5 text-sidebar-foreground/60 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
            !collapsed && "ml-auto",
          )}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          <PanelLeftClose
            className={cn("h-4 w-4 transition-transform", collapsed && "rotate-180")}
          />
        </button>
      </div>

      <nav className="flex-1 px-2 pt-1">
        <ul className="space-y-0.5">
          {(isAdmin ? [...NAV, ...ADMIN_NAV] : NAV).map((item) => {
            const Icon = item.icon;
            const active = item.match(pathname);
            const link = (
              <Link
                href={item.href}
                aria-current={active ? "page" : undefined}
                onMouseEnter={() => item.prefetch?.(qc)}
                onFocus={() => item.prefetch?.(qc)}
                className={cn(
                  "flex h-9 items-center gap-3 rounded-md px-3 text-sm transition-colors cursor-pointer",
                  active
                    ? "bg-sidebar-accent text-sidebar-accent-foreground"
                    : "text-sidebar-foreground/80 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
                )}
              >
                <Icon className="h-4 w-4 shrink-0" />
                {!collapsed && <span>{item.label}</span>}
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
