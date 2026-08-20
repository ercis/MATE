"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback } from "react";
import { ArrowLeft, Inbox, Loader2 } from "lucide-react";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageContainer, PageTitle } from "@/components/page";
import { EmptyState } from "@/components/empty-state";
import { ModuleGrid } from "@/components/processes/module-grid";
import { EventsTab } from "@/components/processes/events-tab";
import { VariantsTab } from "@/components/processes/variants-tab";
import { ActivitiesTab } from "@/components/processes/activities-tab";
import { SettingsTab } from "@/components/processes/settings-tab";
import { FormatBadge } from "@/components/processes/format-badge";
import { OcelOverviewPanel } from "@/components/processes/ocel/ocel-overview-panel";
import { OcelObjectsTab } from "@/components/processes/ocel/ocel-objects-tab";
import { OcelEventsTab } from "@/components/processes/ocel/ocel-events-tab";
import { OcelRelationshipsTab } from "@/components/processes/ocel/ocel-relationships-tab";
import { useEventLog } from "@/lib/queries";
import { formatDateRange, formatNumber, formatRelative } from "@/lib/format";

// Case-centric and object-centric (OCEL) logs get fully separate tab sets – the
// two models never mix.
type TabId =
  | "overview"
  | "events"
  | "variants"
  | "activities"
  | "objects"
  | "relationships"
  | "settings";

const CASE_TAB_IDS: readonly TabId[] = ["overview", "events", "variants", "activities", "settings"];
const OCEL_TAB_IDS: readonly TabId[] = ["overview", "objects", "events", "relationships", "settings"];

function readTab(value: string | null | undefined, allowed: readonly TabId[]): TabId {
  return allowed.includes(value as TabId) ? (value as TabId) : "overview";
}

export function ProcessDetailClient({ logId }: { logId: string }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { data: log, isLoading, isError, error } = useEventLog(logId);

  const setTab = useCallback(
    (next: string) => {
      const sp = new URLSearchParams(searchParams.toString());
      if (next === "overview") sp.delete("tab");
      else sp.set("tab", next);
      // Cross-tab filter params should reset when the user clicks a different tab.
      if (next !== "events") {
        sp.delete("case_id");
        sp.delete("missing_only");
      }
      const query = sp.toString();
      router.replace(query ? `?${query}` : "?", { scroll: false });
    },
    [router, searchParams],
  );

  if (isLoading) {
    return (
      <PageContainer className="space-y-4">
        <Skeleton className="h-8 w-72" />
        <Skeleton className="h-4 w-96" />
        <Skeleton className="h-64 w-full" />
      </PageContainer>
    );
  }
  if (isError || !log) {
    return (
      <EmptyState
        icon={Inbox}
        title="Process not found"
        description={(error as Error)?.message ?? "It may have been deleted."}
      />
    );
  }

  const importing = log.status === "importing";
  const failed = log.status === "failed";
  const ready = log.status === "ready";

  const objectCentric = log.log_model === "object_centric";
  const tabIds = objectCentric ? OCEL_TAB_IDS : CASE_TAB_IDS;
  const tab = readTab(searchParams.get("tab"), tabIds);

  return (
    <PageContainer>
      <Button
        asChild
        variant="ghost"
        size="sm"
        className="mb-4 -ml-2 h-8 cursor-pointer gap-1.5 text-muted-foreground hover:text-foreground"
      >
        <Link href="/processes">
          <ArrowLeft className="h-3.5 w-3.5" />
          Processes
        </Link>
      </Button>
      <header className="space-y-3 pb-6">
        <div className="flex flex-wrap items-center gap-3">
          <PageTitle>{log.name}</PageTitle>
          <FormatBadge format={log.source_format} />
          {objectCentric && (
            <Badge variant="outline" className="border-0 bg-primary/10 text-[10px] uppercase tracking-wide text-primary">
              object-centric
            </Badge>
          )}
          {log.last_edited_at && (
            <Badge variant="outline" className="border-0 bg-muted text-[10px] uppercase tracking-wide text-muted-foreground">
              edited {formatRelative(log.last_edited_at)}
            </Badge>
          )}
        </div>
        {log.description && (
          <p className="text-sm text-muted-foreground max-w-3xl">{log.description}</p>
        )}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted-foreground">
          {objectCentric ? (
            <>
              <span><span className="tabular-nums">{formatNumber(log.object_types_count)}</span> object types</span>
              <span><span className="tabular-nums">{formatNumber(log.objects_count)}</span> objects</span>
              <span><span className="tabular-nums">{formatNumber(log.events_count)}</span> events</span>
            </>
          ) : (
            <>
              <span><span className="tabular-nums">{formatNumber(log.cases_count)}</span> cases</span>
              <span><span className="tabular-nums">{formatNumber(log.events_count)}</span> events</span>
              <span><span className="tabular-nums">{formatNumber(log.variants_count)}</span> variants</span>
            </>
          )}
          <span>{formatDateRange(log.date_min, log.date_max)}</span>
        </div>
      </header>

      {importing && (
        <div className="mb-6 flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-2 text-sm text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          Import is still in progress. Modules become available once it finishes.
        </div>
      )}
      {failed && (
        <div className="mb-6 rounded-lg border border-destructive/20 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          Import failed: {log.error ?? "Unknown error"}
        </div>
      )}
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="overview" className="cursor-pointer">Overview</TabsTrigger>
          {objectCentric ? (
            <>
              <TabsTrigger value="objects" className="cursor-pointer" disabled={!ready}>Objects</TabsTrigger>
              <TabsTrigger value="events" className="cursor-pointer" disabled={!ready}>Events</TabsTrigger>
              <TabsTrigger value="relationships" className="cursor-pointer" disabled={!ready}>Relationships</TabsTrigger>
            </>
          ) : (
            <>
              <TabsTrigger value="events" className="cursor-pointer" disabled={!ready}>Events</TabsTrigger>
              <TabsTrigger value="variants" className="cursor-pointer" disabled={!ready}>Variants</TabsTrigger>
              <TabsTrigger value="activities" className="cursor-pointer" disabled={!ready}>Activities</TabsTrigger>
            </>
          )}
          <TabsTrigger value="settings" className="cursor-pointer">Settings</TabsTrigger>
        </TabsList>
        <TabsContent value="overview" className="pt-6">
          {objectCentric && ready && (
            <div className="mb-6">
              <OcelOverviewPanel logId={logId} />
            </div>
          )}
          <ModuleGrid logId={logId} />
          <p className="mt-6 text-xs text-muted-foreground">
            Need a module that isn&apos;t installed?{" "}
            <Link href="/modules/import" className="underline-offset-4 hover:underline">
              Install one →
            </Link>
          </p>
        </TabsContent>
        {objectCentric ? (
          <>
            <TabsContent value="objects" className="pt-6">
              {ready && <OcelObjectsTab logId={logId} />}
            </TabsContent>
            <TabsContent value="events" className="pt-6">
              {ready && <OcelEventsTab logId={logId} />}
            </TabsContent>
            <TabsContent value="relationships" className="pt-6">
              {ready && <OcelRelationshipsTab logId={logId} />}
            </TabsContent>
          </>
        ) : (
          <>
            <TabsContent value="events" className="pt-6">
              {ready && <EventsTab logId={logId} log={log} />}
            </TabsContent>
            <TabsContent value="variants" className="pt-6">
              {ready && <VariantsTab logId={logId} log={log} />}
            </TabsContent>
            <TabsContent value="activities" className="pt-6">
              {ready && <ActivitiesTab logId={logId} log={log} />}
            </TabsContent>
          </>
        )}
        <TabsContent value="settings" className="pt-6">
          <SettingsTab logId={logId} log={log} />
        </TabsContent>
      </Tabs>
    </PageContainer>
  );
}
