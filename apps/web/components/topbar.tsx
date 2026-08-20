"use client";

import { Fragment, useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Search, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";
import { useUi } from "@/lib/stores/ui";
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import { CommandPalette } from "@/components/cmdk";
import { useEventLogs } from "@/lib/queries";
import { useDashboards } from "@/lib/dashboard-queries";

function isMac() {
  if (typeof navigator === "undefined") return false;
  return /Mac|iPhone|iPad/.test(navigator.platform);
}

function deriveCrumbs(
  pathname: string,
  logNames?: Map<string, string>,
  dashboardNames?: Map<string, string>,
) {
  const parts = pathname.split("/").filter(Boolean);
  if (parts.length === 0) return [{ href: "/processes", label: "Processes", current: true }];
  const out: { href: string; label: string; current: boolean }[] = [];
  let acc = "";
  for (let i = 0; i < parts.length; i++) {
    acc += `/${parts[i]}`;

    // Skip displaying "modules" in breadcrumb
    if (parts[i] === "modules") {
      continue;
    }

    const isLast = i === parts.length - 1;

    // Use the resource's name (not its UUID) when this segment is an id that
    // follows a known collection.
    let label = prettify(parts[i]);
    if (parts[i - 1] === "processes" && logNames?.has(parts[i])) {
      label = logNames.get(parts[i])!;
    } else if (parts[i - 1] === "dashboards" && dashboardNames?.has(parts[i])) {
      label = dashboardNames.get(parts[i])!;
    }

    out.push({
      href: acc,
      label,
      current: isLast,
    });
  }
  return out;
}

// Segments that should render as all-caps acronyms instead of title case.
const ACRONYMS: Record<string, string> = { ai: "AI" };

function prettify(seg: string): string {
  if (/^[0-9a-f-]{8,}$/i.test(seg)) return seg;
  if (ACRONYMS[seg.toLowerCase()]) return ACRONYMS[seg.toLowerCase()];
  return seg.replace(/[-_]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function Topbar() {
  const pathname = usePathname();
  const { data: logs } = useEventLogs();
  const { data: dashboards } = useDashboards();
  const [open, setOpen] = useState(false);

  const logNames = new Map(logs?.map((log) => [log.id, log.name]) ?? []);
  const dashboardNames = new Map(dashboards?.map((d) => [d.id, d.name]) ?? []);
  const crumbs = deriveCrumbs(pathname, logNames, dashboardNames);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen(true);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border bg-background px-4 sm:px-6 lg:px-8">
      <Breadcrumb className="min-w-0 flex-1">
        <BreadcrumbList>
          {crumbs.map((c, i) => (
            // Item and separator must be siblings inside the list – nesting the
            // separator <li> inside the item <li> is invalid HTML (hydration error).
            <Fragment key={c.href}>
              <BreadcrumbItem>
                {c.current ? (
                  <BreadcrumbPage className="truncate">{c.label}</BreadcrumbPage>
                ) : (
                  <BreadcrumbLink asChild>
                    <Link href={c.href}>{c.label}</Link>
                  </BreadcrumbLink>
                )}
              </BreadcrumbItem>
              {i < crumbs.length - 1 && <BreadcrumbSeparator />}
            </Fragment>
          ))}
        </BreadcrumbList>
      </Breadcrumb>

      <MateTopbarButton />

      <Button
        type="button"
        variant="outline"
        size="sm"
        className="cursor-pointer gap-2 text-muted-foreground"
        onClick={() => setOpen(true)}
      >
        <Search className="h-3.5 w-3.5" />
        <span className="hidden md:inline">Search</span>
        <kbd className="ml-2 rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
          {isMac() ? "⌘K" : "Ctrl+K"}
        </kbd>
      </Button>

      <CommandPalette open={open} onOpenChange={setOpen} />
    </header>
  );
}

function MateTopbarButton() {
  const open = useUi((s) => s.mateOpen);
  const toggle = useUi((s) => s.toggleMate);
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      className={cn(
        "cursor-pointer gap-1.5 text-muted-foreground",
        open && "text-foreground",
      )}
      onClick={toggle}
      aria-label="Toggle MATE AI"
      aria-pressed={open}
    >
      <Sparkles className="h-3.5 w-3.5" />
      <span className="hidden md:inline">MATE AI</span>
    </Button>
  );
}

