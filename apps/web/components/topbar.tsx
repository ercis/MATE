"use client";

import { Fragment, useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { ArrowLeft, Info, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";
import { useUi } from "@/lib/stores/ui";
import {
  Breadcrumb,
  BreadcrumbEllipsis,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { ModuleAboutContent } from "@/components/modules/module-about";
import { useEventLogs, useModules } from "@/lib/queries";
import { useDashboards } from "@/lib/dashboard-queries";
import { resolvePageMeta, type ModuleAbout, type PageMeta } from "@/lib/page-meta";

interface Crumb {
  href: string;
  label: string;
  current: boolean;
}

function deriveCrumbs(
  pathname: string,
  logNames: Map<string, string>,
  dashboardNames: Map<string, string>,
  moduleNames: Map<string, string>,
): Crumb[] {
  const parts = pathname.split("/").filter(Boolean);
  if (parts.length === 0) return [{ href: "/processes", label: "Processes", current: true }];
  const out: Crumb[] = [];
  let acc = "";
  for (let i = 0; i < parts.length; i++) {
    acc += `/${parts[i]}`;

    // Skip the intermediate "modules" segment inside the process module view
    // (…/processes/{id}/modules/{modId}), but keep the top-level /modules
    // section so it shows in the breadcrumb like every other section.
    if (parts[i] === "modules" && i > 0) {
      continue;
    }

    const isLast = i === parts.length - 1;

    // The intermediate "variants" segment (…/processes/{id}/variants/{variantId})
    // has no page of its own — send its crumb (and parent-mode back) to the
    // process page's Variants tab instead of a 404.
    if (parts[i] === "variants" && i > 0 && !isLast) {
      out.push({ href: `/processes/${parts[i - 1]}?tab=variants`, label: "Variants", current: false });
      continue;
    }

    // Use the resource's name (not its UUID/slug) when this segment is an id
    // that follows a known collection.
    let label = parts[i] === "modules" ? "Module Settings" : prettify(parts[i]);
    if (parts[i - 1] === "processes" && logNames.has(parts[i])) {
      label = logNames.get(parts[i])!;
    } else if (parts[i - 1] === "dashboards" && dashboardNames.has(parts[i])) {
      label = dashboardNames.get(parts[i])!;
    } else if (parts[i - 1] === "modules" && moduleNames.has(parts[i])) {
      label = moduleNames.get(parts[i])!;
    } else if (parts[i - 1] === "variants") {
      label = "Variant";
    } else if (parts[i] === "activities" && i > 1) {
      // The activity detail route (…/processes/{id}/activities?name=…) — the
      // segment is plural but the page shows one activity.
      label = "Activity";
    }

    out.push({ href: acc, label, current: isLast });
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

// Pages you land on directly from the sidebar (or the user menu) are top-level –
// there is nowhere sensible to go "back" to, so the back arrow is hidden. Their
// sub-pages / detail views (drilled into) keep it. Settings and Admin are tabbed
// areas whose tabs are peers, so the whole section counts as top-level.
function isTopLevelRoute(pathname: string): boolean {
  const p = pathname.replace(/\/+$/, "") || "/";
  if (p === "/" || p === "/processes" || p === "/dashboards" || p === "/modules") return true;
  if (p === "/profile") return true;
  if (p.startsWith("/settings") || p.startsWith("/admin")) return true;
  return false;
}

export function Topbar() {
  const pathname = usePathname();
  const router = useRouter();
  const { data: logs } = useEventLogs();
  const { data: dashboards } = useDashboards();
  const { data: modules } = useModules();

  const logNames = new Map(logs?.map((log) => [log.id, log.name]) ?? []);
  const dashboardNames = new Map(dashboards?.map((d) => [d.id, d.name]) ?? []);
  const moduleNames = new Map(modules?.map((m) => [m.id, m.name]) ?? []);
  const moduleMeta = new Map<string, ModuleAbout>(
    modules?.map((m) => [
      m.id,
      {
        name: m.name,
        description: m.description,
        about: m.about,
        sources: m.source,
        artifacts: m.artifacts,
        license: m.license,
        version: m.version,
      },
    ]) ?? [],
  );

  const crumbs = deriveCrumbs(pathname, logNames, dashboardNames, moduleNames);
  const meta = resolvePageMeta(pathname, {
    logNames,
    dashboardNames,
    modules: moduleMeta,
  });
  const showBack = !isTopLevelRoute(pathname);
  const backButtonMode = useUi((s) => s.backButtonMode);

  // "parent" = up one level in the breadcrumb hierarchy; "history" (default)
  // = browser back. Falls back to history when there's no parent crumb to
  // go to (e.g. a single-segment drill-in route).
  const handleBack = () => {
    if (backButtonMode === "parent" && crumbs.length >= 2) {
      router.push(crumbs[crumbs.length - 2].href);
      return;
    }
    router.back();
  };

  return (
    <header className="grid h-14 shrink-0 grid-cols-[1fr_auto_1fr] items-center gap-3 border-b border-white/10 bg-background/70 px-4 backdrop-blur-xl backdrop-saturate-150 supports-[backdrop-filter]:bg-background/55 sm:px-6 lg:px-8">
      {/* Left: back arrow (drill-in pages only) */}
      <div className="flex min-w-0 items-center">
        {showBack && (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-8 w-8 shrink-0 cursor-pointer text-muted-foreground hover:text-foreground"
            onClick={handleBack}
            aria-label="Go back"
          >
            <ArrowLeft className="h-4 w-4" />
          </Button>
        )}
      </div>

      {/* Center: animated breadcrumb trail (current page bold) + info */}
      <AnimatedCrumbs pathname={pathname} crumbs={crumbs} meta={meta} />

      {/* Right: MATE AI */}
      <div className="flex min-w-0 items-center justify-end">
        <MateTopbarButton />
      </div>
    </header>
  );
}

// The trail is keyed on pathname inside AnimatePresence, so each navigation
// animates the outgoing trail out and the incoming one in (a short fade + slide).
// `initial={false}` suppresses the animation on the very first paint / refresh.
function AnimatedCrumbs({
  pathname,
  crumbs,
  meta,
}: {
  pathname: string;
  crumbs: Crumb[];
  meta: PageMeta;
}) {
  const reduce = useReducedMotion();
  const offset = reduce ? 0 : 5;

  return (
    <div className="flex min-w-0 items-center justify-center">
      <AnimatePresence mode="wait" initial={false}>
        <motion.div
          key={pathname}
          initial={{ opacity: 0, y: -offset }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: offset }}
          transition={{ duration: reduce ? 0 : 0.16, ease: "easeOut" }}
          className="flex min-w-0 items-center justify-center gap-1"
        >
          <TrimmedBreadcrumbs crumbs={crumbs} />
          {/* ⓘ only on in-module pages – the module "About" card. */}
          {meta.module && <PageInfoButton title={meta.title} module={meta.module} />}
        </motion.div>
      </AnimatePresence>
    </div>
  );
}

// How many crumbs to show before collapsing the middle into an ellipsis. Scales
// with viewport width: wide screens keep the full trail, narrow ones collapse
// sooner. Mobile-first default (3) drives SSR + the first pre-hydration paint.
const CRUMB_BREAKPOINTS: Array<[query: string, max: number]> = [
  ["(min-width: 1536px)", 10], // 2xl – effectively never collapses
  ["(min-width: 1280px)", 8], // xl
  ["(min-width: 1024px)", 6], // lg
  ["(min-width: 768px)", 5], // md
  ["(min-width: 640px)", 4], // sm
];

function useMaxCrumbs(): number {
  const [max, setMax] = useState(3);
  useEffect(() => {
    const mqls = CRUMB_BREAKPOINTS.map(([q]) => window.matchMedia(q));
    const update = () => {
      const hit = CRUMB_BREAKPOINTS.find(([q]) => window.matchMedia(q).matches);
      setMax(hit ? hit[1] : 3);
    };
    update();
    mqls.forEach((m) => m.addEventListener("change", update));
    return () => mqls.forEach((m) => m.removeEventListener("change", update));
  }, []);
  return max;
}

// Collapse the middle of a long trail into an ellipsis (first crumb + last two
// kept), so deep routes stay one line. The collapse threshold is viewport-driven
// (see useMaxCrumbs). Each visible crumb also truncates individually; the current
// (last) crumb renders as an accent pill.
function TrimmedBreadcrumbs({ crumbs }: { crumbs: Crumb[] }) {
  const maxCrumbs = useMaxCrumbs();
  type Node = { kind: "crumb"; crumb: Crumb } | { kind: "ellipsis"; hidden: Crumb[] };
  const nodes: Node[] =
    crumbs.length <= maxCrumbs
      ? crumbs.map((crumb) => ({ kind: "crumb", crumb }))
      : [
          { kind: "crumb", crumb: crumbs[0] },
          { kind: "ellipsis", hidden: crumbs.slice(1, -2) },
          { kind: "crumb", crumb: crumbs[crumbs.length - 2] },
          { kind: "crumb", crumb: crumbs[crumbs.length - 1] },
        ];

  return (
    <Breadcrumb className="min-w-0">
      <BreadcrumbList className="flex-nowrap">
        {nodes.map((node, i) => (
          <Fragment key={node.kind === "crumb" ? node.crumb.href : `ellipsis-${i}`}>
            <BreadcrumbItem className="min-w-0">
              {node.kind === "ellipsis" ? (
                <Popover>
                  <PopoverTrigger
                    className="flex cursor-pointer items-center rounded-sm text-muted-foreground hover:text-foreground"
                    aria-label="Show hidden path segments"
                  >
                    <BreadcrumbEllipsis className="size-5" />
                  </PopoverTrigger>
                  <PopoverContent align="center" className="w-auto min-w-40 p-1.5">
                    <div className="flex flex-col">
                      {node.hidden.map((c) => (
                        <Link
                          key={c.href}
                          href={c.href}
                          className="truncate rounded-sm px-2 py-1.5 text-sm text-muted-foreground hover:bg-accent hover:text-foreground"
                        >
                          {c.label}
                        </Link>
                      ))}
                    </div>
                  </PopoverContent>
                </Popover>
              ) : node.crumb.current ? (
                <BreadcrumbPage className="max-w-[14rem] truncate rounded-md bg-primary/10 px-2 py-0.5 text-[15px] font-semibold text-primary lg:max-w-[22rem] 2xl:max-w-[30rem]">
                  {node.crumb.label}
                </BreadcrumbPage>
              ) : (
                <BreadcrumbLink asChild className="max-w-[10rem] truncate lg:max-w-[18rem] 2xl:max-w-[24rem]">
                  <Link href={node.crumb.href}>{node.crumb.label}</Link>
                </BreadcrumbLink>
              )}
            </BreadcrumbItem>
            {i < nodes.length - 1 && <BreadcrumbSeparator />}
          </Fragment>
        ))}
      </BreadcrumbList>
    </Breadcrumb>
  );
}

function PageInfoButton({ title, module }: { title: string; module: ModuleAbout }) {
  return (
    <Popover>
      <PopoverTrigger
        className="flex h-6 w-6 shrink-0 cursor-pointer items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
        aria-label={`About “${title}”`}
      >
        <Info className="h-3.5 w-3.5" />
      </PopoverTrigger>
      <PopoverContent align="center" className="w-96 max-w-[calc(100vw-2rem)] space-y-3">
        <ModuleAboutContent {...module} />
      </PopoverContent>
    </Popover>
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
