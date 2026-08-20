"use client";

import Link from "next/link";
import { Loader2 } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/cn";
import { hasActiveModuleJob, useJobsStore } from "@/lib/stores/jobs";
import type { ModuleSummary } from "@/lib/api-types";

interface ModuleCardProps {
  module: ModuleSummary;
  logId: string;
}

export function ModuleCard({ module, logId }: ModuleCardProps) {
  const status = module.availability?.status ?? "available";
  const reasons = module.availability?.reasons ?? [];

  // Block opening while a job for this (log, module) is queued/running/paused.
  const isJobRunning = useJobsStore((s) => hasActiveModuleJob(s, logId, module.id));

  const isDisabled = module.enabled === false;
  const isAvailable = !isDisabled && !isJobRunning && status === "available";
  const isDegraded = !isDisabled && !isJobRunning && status === "degraded";
  const isUnavailable = !isDisabled && !isJobRunning && status === "unavailable";

  const tooltipReasons = isJobRunning
    ? ["A job for this module is currently running. Wait for it to finish before opening."]
    : isDisabled
    ? ["Disabled in Settings → Modules. Enable it to open the module page."]
    : reasons;

  // A real <Link> (not router.push) so navigation flows through an <a>: that
  // lets the global RouteProgress capture-listener fire the top loading bar,
  // enables cmd/ctrl-click, and matches the rest of the app's nav. Non-openable
  // cards render as a plain, non-interactive Card.
  const navigable = isAvailable || isDegraded;
  const href = `/processes/${logId}/modules/${module.id}`;

  const card = (
    <Card
      className={cn(
        // Tile-style card: drop the default outer py/gap so CardContent's
        // p-4 fully owns the card's padding.
        "group relative flex h-full flex-col gap-0 py-0 transition-all",
        isAvailable && "cursor-pointer hover:-translate-y-0.5 hover:shadow-md",
        isDegraded && "cursor-pointer hover:shadow-md",
        (isUnavailable || isDisabled || isJobRunning) && "cursor-not-allowed opacity-60",
      )}
      aria-disabled={isUnavailable || isDisabled || isJobRunning}
    >
      <CardContent className="flex h-full flex-col gap-3 p-4">
        {/* Header: Name, version, author */}
        <div className="min-w-0">
          <h3 className="truncate text-sm font-semibold leading-tight">{module.name}</h3>
          <div className="mt-0.5 flex items-center gap-2 text-[10px] text-muted-foreground">
            {module.version && <span className="shrink-0">{module.version}</span>}
            {module.author && module.version && <span className="shrink-0 text-muted-foreground/50">·</span>}
            {module.author && <span className="truncate">by {module.author}</span>}
          </div>
        </div>

        {/* Status */}
        <div className="flex flex-wrap items-center gap-1.5 empty:hidden">
          {isJobRunning && (
            <Badge className="h-5 gap-1 border-0 bg-primary/10 px-2 py-0 text-[9px] font-medium text-primary">
              <Loader2 className="h-2.5 w-2.5 animate-spin" />
              Running
            </Badge>
          )}
          {!isJobRunning && isDisabled && (
            <Badge className="h-5 border-0 bg-muted px-2 py-0 text-[9px] font-medium text-muted-foreground">
              Disabled
            </Badge>
          )}
          {!isJobRunning && !isDisabled && isDegraded && (
            <Badge className="h-5 border-0 bg-amber-500/15 px-2 py-0 text-[9px] font-medium text-amber-700 dark:text-amber-400">
              Limited
            </Badge>
          )}
          {!isJobRunning && !isDisabled && isUnavailable && (
            <Badge className="h-5 border-0 bg-destructive/10 px-2 py-0 text-[9px] font-medium text-destructive">
              Unavailable
            </Badge>
          )}
        </div>

        {/* Description */}
        {module.description && (
          <p className="line-clamp-2 flex-1 text-xs leading-snug text-muted-foreground">
            {module.description}
          </p>
        )}
      </CardContent>
    </Card>
  );

  const content = navigable ? (
    <Link
      href={href}
      className="block h-full rounded-xl outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
    >
      {card}
    </Link>
  ) : (
    card
  );

  if (tooltipReasons.length === 0) return content;

  return (
    <Tooltip>
      <TooltipTrigger asChild>{content}</TooltipTrigger>
      <TooltipContent side="bottom" className="max-w-sm">
        <ul className="list-disc pl-4 text-xs">
          {tooltipReasons.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      </TooltipContent>
    </Tooltip>
  );
}
