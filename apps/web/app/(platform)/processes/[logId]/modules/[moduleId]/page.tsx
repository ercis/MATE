"use client";

import { useEffect } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { FileBox } from "lucide-react";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/empty-state";
import { PageContainer } from "@/components/page";
import { PanelSkeleton } from "@/components/skeletons";
import { useModules } from "@/lib/queries";
import { getModulePanel, prefetchModulePanel } from "@/lib/module-panels";
import { LogFilterProvider } from "@/components/processes/log-filter";

export default function ModulePage() {
  const params = useParams<{ logId: string; moduleId: string }>();
  const { logId, moduleId } = params;

  // Start the panel bundle + runtime on mount rather than after `useModules`
  // resolves. The route already knows which module it is, so gating the two
  // large transfers behind the module-list round trip only serialised them.
  // A deep link or reload (no hover prefetch from the grid) used to pay all
  // three back to back. Rendering still waits for the query below - this only
  // moves the fetch off the critical path, it does not mount anything early.
  useEffect(() => {
    prefetchModulePanel(moduleId);
  }, [moduleId]);

  const { data: modules, isLoading, isError } = useModules(logId);

  const mod = modules?.find((m) => m.id === moduleId);

  // Same shell as loading.tsx, so the route-level and data-level skeletons are
  // interchangeable - no flash or jump when one hands over to the other.
  if (isLoading) {
    return <PanelSkeleton />;
  }
  if (isError) {
    return (
      <EmptyState
        icon={FileBox}
        title="Couldn't load module"
        description="The module loader is offline or failed to start."
      />
    );
  }
  if (!mod) {
    return (
      <EmptyState
        icon={FileBox}
        title={`Module "${moduleId}" not found`}
        description="It may not be installed or may have failed to load."
        primaryAction={
          <Button asChild className="cursor-pointer">
            <Link href="/modules/import">Install a module</Link>
          </Button>
        }
      />
    );
  }

  return (
    <PageContainer>
      {/* Deep links can reach a module the grid renders grayed-out (disabled,
          or the log doesn't meet its manifest requirements). Mounting the
          panel would just fail on its first query – explain instead. */}
      {mod.enabled === false ? (
        <div className="rounded-xl border border-dashed border-border bg-card/40 px-6 py-16 text-center">
          <p className="text-sm font-medium">This module is disabled</p>
          <p className="mt-2 text-sm text-muted-foreground">
            Enable it on the{" "}
            <Link href={`/modules/${mod.id}`} className="underline underline-offset-2">
              Modules
            </Link>{" "}
            page to open it.
          </p>
        </div>
      ) : (mod.availability?.status ?? "available") === "unavailable" ? (
        <div className="rounded-xl border border-dashed border-border bg-card/40 px-6 py-16 text-center">
          <p className="text-sm font-medium">Not available for this process</p>
          <ul className="mt-2 space-y-1 text-sm text-muted-foreground">
            {(mod.availability?.reasons ?? ["This process doesn't meet the module's requirements."]).map(
              (r, i) => (
                <li key={i}>{r}</li>
              ),
            )}
          </ul>
        </div>
      ) : (
        // Log-scoped global filter: sets the active log's ephemeral event filter
        // and re-scopes this (and every other) module view for the log. Wraps
        // the panel so its data queries pick up the `X-FF-Event-Filter` header.
        // Modules opt out via the manifest (`frontend.log_filter: false`) and
        // mount without the bar or the header.
        <LogFilterProvider logId={logId} enabled={mod.supports_log_filter}>
          <ModulePanelSlot logId={logId} moduleId={mod.id} hasFrontend={mod.has_frontend} />
        </LogFilterProvider>
      )}
    </PageContainer>
  );
}

function ModulePanelSlot({
  logId,
  moduleId,
  hasFrontend,
}: {
  logId: string;
  moduleId: string;
  hasFrontend: boolean;
}) {
  const Panel = getModulePanel(moduleId, { hasFrontend });
  if (Panel) {
    return <Panel logId={logId} moduleId={moduleId} />;
  }
  return (
    <div className="rounded-xl border border-dashed border-border bg-card/40 px-6 py-16 text-center">
      <p className="text-sm text-muted-foreground">
        This module has no frontend panel yet. The platform mounts its API at{" "}
        <code className="rounded bg-muted px-1 text-[11px]">/api/v1/modules/{moduleId}/…</code>.
      </p>
    </div>
  );
}
