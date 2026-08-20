"use client";

import { useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, FileBox } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/empty-state";
import { PageContainer, PageTitle } from "@/components/page";
import { useEventLog, useModules } from "@/lib/queries";
import { getModulePanel } from "@/lib/module-panels";

export default function ModulePage() {
  const params = useParams<{ logId: string; moduleId: string }>();
  const { logId, moduleId } = params;

  const { data: log } = useEventLog(logId);
  const { data: modules, isLoading, isError } = useModules(logId);

  const mod = modules?.find((m) => m.id === moduleId);

  if (isLoading) {
    return (
      <PageContainer className="space-y-4">
        <Skeleton className="h-8 w-72" />
        <Skeleton className="h-4 w-96" />
        <Skeleton className="h-96 w-full" />
      </PageContainer>
    );
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
      <header className="flex items-start gap-3 pb-6">
        <div className="space-y-1">
          <Button asChild variant="ghost" size="sm" className="cursor-pointer -ml-2 gap-1">
            <Link href={`/processes/${logId}`}>
              <ArrowLeft className="h-3.5 w-3.5" />
              <span>{log?.name ?? "Back"}</span>
            </Link>
          </Button>
          <div className="flex items-center gap-2">
            <PageTitle>{mod.name}</PageTitle>
            <Badge variant="outline" className="border-0 bg-muted text-[10px] uppercase">
              {mod.category.replace("_", " ")}
            </Badge>
          </div>
          {mod.description && (
            <p className="max-w-2xl text-sm text-muted-foreground">{mod.description}</p>
          )}
        </div>
      </header>

      <ModulePanelSlot logId={logId} moduleId={mod.id} hasFrontend={mod.has_frontend} />
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
