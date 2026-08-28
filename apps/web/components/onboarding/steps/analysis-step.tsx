"use client";

import { PackageCheck, SlidersHorizontal, Workflow } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useModules } from "@/lib/queries";

/**
 * The wizard's closing screen: what modules are, that they are already on, and
 * when they run.
 *
 * This replaced a "Choose your modules" toggle list. That screen asked for a
 * decision with no consequence - every bundled module is installed and enabled
 * by default, and `enabled: false` only hides cards; the precompute closure is
 * keyed off *installs*, so a disabled module still runs on import and still
 * gates the log's readiness. It also line-clamped 15 of 17 descriptions to one
 * line behind a hover-only tooltip.
 *
 * So the screen answers the question the toggles implied instead: nothing to
 * pick here, here is what you got, here is when it computes. Real per-module
 * control lives on `/modules`, where the config guard and the admin lock are.
 */
export function AnalysisStep() {
  const { data: modules, isLoading } = useModules(null);
  const count = modules?.length ?? 0;

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-8">
      <div className="space-y-2 text-center">
        <h1 className="text-3xl font-semibold tracking-tight">Your analysis is already set up</h1>
        <p className="text-sm text-muted-foreground">
          {isLoading || count === 0
            ? "Your analysis modules are installed and switched on — there is nothing to choose here."
            : `PM-MATE ships with ${count} analysis modules. All of them are installed and switched on for your account — there is nothing to choose here.`}
        </p>
      </div>

      <div className="space-y-3">
        <Point
          icon={PackageCheck}
          title="Modules do the analysis"
          body="Discovery, performance, conformance, complexity, drift detection and more. Each one is a self-contained analysis that runs against a single event log."
        />
        <Point
          icon={Workflow}
          title="They compute the moment a log arrives"
          body="As soon as an import finishes parsing, one job per module is queued and they run in dependency order. The process reads “Preparing modules…” until they're done — after that every module opens instantly. You can watch the steps in the Jobs panel at the bottom of the left sidebar."
        />
        <Point
          icon={SlidersHorizontal}
          title="Fine-tune later, not now"
          body="Turn modules off, change their settings, or install new ones from Modules in the left sidebar. Nothing here is permanent."
        />
      </div>

      {isLoading ? (
        <div className="flex flex-wrap justify-center gap-1.5">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-6 w-24 rounded-full" />
          ))}
        </div>
      ) : (
        count > 0 && (
          <div className="flex flex-wrap justify-center gap-1.5">
            {modules?.map((m) => (
              <Badge key={m.id} variant="secondary" className="font-normal">
                {m.name}
              </Badge>
            ))}
          </div>
        )
      )}
    </div>
  );
}

function Point({
  icon: Icon,
  title,
  body,
}: {
  icon: typeof PackageCheck;
  title: string;
  body: string;
}) {
  return (
    <div className="flex gap-3 rounded-xl border border-border bg-surface p-4">
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
        <Icon className="h-4 w-4" />
      </div>
      <div className="min-w-0 space-y-0.5">
        <div className="text-sm font-semibold">{title}</div>
        <p className="text-xs leading-relaxed text-muted-foreground">{body}</p>
      </div>
    </div>
  );
}
