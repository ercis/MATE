"use client";

import Link from "next/link";
import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { FolderPlus, Inbox, Plus, RefreshCw, Upload } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  PageContainer,
  PageHeader,
  PageTitle,
  PageDescription,
  PageActions,
} from "@/components/page";
import { EmptyState } from "@/components/empty-state";
import {
  NewFolderDialog,
  ProcessesTable,
} from "@/components/processes/processes-table";
import { useEventLogs } from "@/lib/queries";
import { cn } from "@/lib/cn";
import type { LogModel } from "@/lib/api-types";

export function ProcessesClient() {
  const [newFolderOpen, setNewFolderOpen] = useState(false);
  return (
    <PageContainer>
      <Header onNewFolder={() => setNewFolderOpen(true)} />
      <Suspense fallback={<ListSkeleton />}>
        <ProcessList />
      </Suspense>
      <NewFolderDialog open={newFolderOpen} onOpenChange={setNewFolderOpen} />
    </PageContainer>
  );
}

function Header({ onNewFolder }: { onNewFolder: () => void }) {
  return (
    <PageHeader>
      <div className="space-y-1">
        <PageTitle>Processes</PageTitle>
        <PageDescription>
          Imported event logs. Drop a XES, XES.gz, or CSV here to start mining.
        </PageDescription>
      </div>
      <PageActions>
        <Button variant="outline" asChild className="gap-2 cursor-pointer">
          <Link href="/processes/watched">
            <RefreshCw className="h-4 w-4" />
            Watched folders
          </Link>
        </Button>

        <Button variant="outline" onClick={onNewFolder} className="gap-2 cursor-pointer">
          <FolderPlus className="h-4 w-4" />
          New folder
        </Button>

        <Button asChild className="gap-2 cursor-pointer">
          <Link href="/processes/import">
            <Upload className="h-4 w-4" />
            Import event log
          </Link>
        </Button>
      </PageActions>
    </PageHeader>
  );
}

function ListSkeleton() {
  return (
    <div className="space-y-2">
      {Array.from({ length: 4 }).map((_, i) => (
        <Skeleton key={i} className="h-12 w-full" />
      ))}
    </div>
  );
}

type ModelFilter = "all" | LogModel;

const MODEL_FILTERS: { value: ModelFilter; label: string }[] = [
  { value: "all", label: "All processes" },
  { value: "case_centric", label: "Case-centric" },
  { value: "object_centric", label: "Object-centric" },
];

function ProcessList() {
  const sp = useSearchParams();
  const q = sp.get("q") ?? undefined;
  const status = sp.get("status") ?? undefined;
  const [model, setModel] = useState<ModelFilter>("all");
  const { data, isLoading, isError, error } = useEventLogs({ q, status });

  if (isLoading) return <ListSkeleton />;
  if (isError) {
    return (
      <EmptyState
        icon={Inbox}
        title="Couldn't load processes"
        description={(error as Error)?.message ?? "Unknown error"}
      />
    );
  }
  if (!data || data.length === 0) {
    return (
      <EmptyState
        icon={Inbox}
        title="Import your first event log"
        description="Drop a XES, XES.gz, or CSV to start. The platform stores it as Parquet so analytics modules can query it in milliseconds."
        primaryAction={
          <Button asChild className="cursor-pointer">
            <Link href="/processes/import" className="gap-2">
              <Upload className="h-4 w-4" />
              Import event log
            </Link>
          </Button>
        }
        secondaryAction={
          <Button variant="outline" disabled className="gap-2 cursor-not-allowed">
            <Plus className="h-4 w-4" />
            Try with sample data
          </Button>
        }
      />
    );
  }

  const rows = model === "all" ? data : data.filter((l) => l.log_model === model);

  return (
    <div className="space-y-3">
      <div className="inline-flex w-fit items-center gap-1 rounded-lg border border-border bg-muted/50 p-1">
        {MODEL_FILTERS.map(({ value, label }) => (
          <Button
            key={value}
            type="button"
            size="sm"
            variant="ghost"
            aria-pressed={model === value}
            className={cn(
              "cursor-pointer border-0 shadow-none",
              model === value
                ? "bg-primary text-primary-foreground hover:bg-primary/90 hover:text-primary-foreground"
                : "text-muted-foreground hover:bg-transparent hover:text-foreground",
            )}
            onClick={() => setModel(value)}
          >
            {label}
          </Button>
        ))}
      </div>
      {rows.length === 0 ? (
        <EmptyState
          icon={Inbox}
          title="No matching processes"
          description={`No ${
            model === "case_centric" ? "case-centric" : "object-centric"
          } logs yet. Switch to “All processes” to see everything.`}
        />
      ) : (
        <div className="overflow-hidden rounded-xl border border-border bg-card">
          <ProcessesTable rows={rows} />
        </div>
      )}
    </div>
  );
}
