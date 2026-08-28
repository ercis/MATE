"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState, useTransition } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Inbox, Settings2 } from "lucide-react";

import { Tabs, TabsContent } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageContainer } from "@/components/page";
import { DetailSkeleton } from "@/components/skeletons";
import { EmptyState } from "@/components/empty-state";
import { ModuleGrid, ModuleSearchBar } from "@/components/processes/module-grid";
import { ProcessTabs, type ProcessTabItem } from "@/components/processes/process-tabs";
import { EventsTab } from "@/components/processes/events-tab";
import { VariantsTab } from "@/components/processes/variants-tab";
import { ActivitiesTab } from "@/components/processes/activities-tab";
import { SettingsTab } from "@/components/processes/settings-tab";
import { OcelOverviewPanel } from "@/components/processes/ocel/ocel-overview-panel";
import { LogStatStrip } from "@/components/processes/log-stat-strip";
import { LogStatusBanner } from "@/components/processes/log-status-banner";
import { OcelObjectsTab } from "@/components/processes/ocel/ocel-objects-tab";
import { OcelEventsTab } from "@/components/processes/ocel/ocel-events-tab";
import { OcelRelationshipsTab } from "@/components/processes/ocel/ocel-relationships-tab";
import { useEventLog } from "@/lib/queries";
import { prefetchProcessTabs } from "@/lib/client-prefetch";
import { formatRelative } from "@/lib/format";

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
  const qc = useQueryClient();
  const { data: log, isLoading, isError, error } = useEventLog(logId);
  // Module search lives in the tab row (see below), so its state is lifted out
  // of ModuleGrid.
  const [moduleQuery, setModuleQuery] = useState("");

  // The active tab is URL-backed (?tab=) so it deep-links and survives refresh,
  // but reading it back through a navigation roundtrip made every click feel
  // like click → wait → switch. Local state is the visible source of truth so a
  // click flips instantly and never reverts; the URL syncs in the background
  // transition, and the effect reconciles both (also browser back/forward).
  const objectCentric = log?.log_model === "object_centric";
  const tabIds = objectCentric ? OCEL_TAB_IDS : CASE_TAB_IDS;
  const urlTab = readTab(searchParams.get("tab"), tabIds);
  const [tab, setTabState] = useState<TabId>(urlTab);
  const [, startTransition] = useTransition();
  useEffect(() => {
    setTabState(urlTab);
  }, [urlTab]);

  // Warm the other tabs' first-page queries once the log is ready, so a tab
  // click renders from cache instead of a skeleton. prefetchQuery no-ops while
  // the data is still fresh, so refires (log refetches) are cheap.
  useEffect(() => {
    if (log?.status === "ready") prefetchProcessTabs(qc, log);
  }, [qc, log]);

  const setTab = useCallback(
    (next: string) => {
      const tabId = readTab(next, tabIds);
      setTabState(tabId); // instant switch, never reverts
      startTransition(() => {
        // URL sync (deep-link source of truth) runs in the background so the
        // click never blocks on the navigation roundtrip.
        const sp = new URLSearchParams(searchParams.toString());
        if (tabId === "overview") sp.delete("tab");
        else sp.set("tab", tabId);
        // Cross-tab filter params should reset when the user clicks a different tab.
        if (tabId !== "events") {
          sp.delete("case_id");
          sp.delete("missing_only");
        }
        if (tabId !== "variants") {
          sp.delete("activity");
        }
        const query = sp.toString();
        router.replace(query ? `?${query}` : "?", { scroll: false });
      });
    },
    [router, searchParams, tabIds],
  );

  if (isLoading) {
    // Same shell as loading.tsx, so the route-level and data-level skeletons
    // are interchangeable - no flash or jump when one hands over to the other.
    return <DetailSkeleton />;
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

  const ready = log.status === "ready";

  // Counts only appear once the log is ready; the data tabs are disabled until
  // then. Two disjoint tab sets — case-centric vs object-centric (OCEL).
  const tabItems: ProcessTabItem[] = objectCentric
    ? [
        { value: "overview", label: "Overview" },
        { value: "objects", label: "Objects", count: ready ? log.objects_count : null, disabled: !ready },
        { value: "events", label: "Events", count: ready ? log.events_count : null, disabled: !ready },
        {
          value: "relationships",
          label: "Relationships",
          count: ready ? log.relations_count : null,
          disabled: !ready,
        },
        { value: "settings", label: "Settings" },
      ]
    : [
        { value: "overview", label: "Overview" },
        { value: "events", label: "Events", count: ready ? log.events_count : null, disabled: !ready },
        { value: "variants", label: "Variants", count: ready ? log.variants_count : null, disabled: !ready },
        { value: "activities", label: "Activities", disabled: !ready },
        { value: "settings", label: "Settings" },
      ];

  return (
    <Tabs value={tab} onValueChange={setTab}>
      {/* Full-bleed like the global Topbar (no max-w cap) so it reads as an
          extension of it – flush underneath, no dead gap above. The inner
          div re-applies PageContainer's cap+padding so triggers still align
          with the capped content below. */}
      <div className="sticky top-0 z-20 border-b border-border bg-background/80 backdrop-blur">
        <div className="mx-auto flex w-full max-w-[1760px] items-center gap-3 px-4 sm:px-6 lg:px-8">
          <ProcessTabs items={tabItems} value={tab} className="min-w-0" />
          {tab === "overview" && (
            <ModuleSearchBar
              query={moduleQuery}
              onQueryChange={setModuleQuery}
              className="ml-auto shrink-0"
            />
          )}
        </div>
      </div>

      <PageContainer>
        {(objectCentric || log.last_edited_at || log.description) && (
          <header className="space-y-3 pb-6">
            {(objectCentric || log.last_edited_at) && (
              <div className="flex flex-wrap items-center gap-2">
                {objectCentric && (
                  <Badge variant="outline" className="border-0 bg-primary/10 text-[10px] uppercase tracking-wide text-primary">
                    object-centric
                  </Badge>
                )}
                {log.last_edited_at && (
                  <span className="text-[11px] text-muted-foreground">
                    edited {formatRelative(log.last_edited_at)}
                  </span>
                )}
              </div>
            )}
            {log.description && (
              <p className="max-w-3xl text-sm text-muted-foreground">{log.description}</p>
            )}
          </header>
        )}

        {log.mapping_needs_review && ready && (
          <div className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-amber-500/25 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-300">
            <AlertTriangle className="h-4 w-4 shrink-0" />
            <span className="flex-1">
              The importer guessed one or more required columns. Confirm the mapping so analytics stay correct.
            </span>
            <Button
              size="sm"
              variant="outline"
              className="h-7 shrink-0 cursor-pointer gap-1.5 border-amber-500/30 bg-transparent text-amber-700 hover:bg-amber-500/15 dark:text-amber-300"
              onClick={() => setTab("settings")}
            >
              <Settings2 className="h-3.5 w-3.5" />
              Review mapping
            </Button>
          </div>
        )}
        <LogStatusBanner log={log} />

        <TabsContent value="overview">
          {/* OCEL gets its own native summary; case-centric logs get the stat
              strip (also the tour's "read the log's shape" anchor). */}
          {objectCentric ? (
            ready && (
              <div className="mb-6">
                <OcelOverviewPanel logId={logId} />
              </div>
            )
          ) : (
            <div className="mb-6">
              <LogStatStrip log={log} />
            </div>
          )}
          <ModuleGrid
            logId={logId}
            query={moduleQuery}
            onQueryChange={setModuleQuery}
          />
          <p className="mt-6 text-xs text-muted-foreground">
            Need a module that isn&apos;t installed?{" "}
            <Link href="/modules/import" className="underline-offset-4 hover:underline">
              Install one →
            </Link>
          </p>
        </TabsContent>
        {objectCentric ? (
          <>
            <TabsContent value="objects">
              {ready && <OcelObjectsTab logId={logId} />}
            </TabsContent>
            <TabsContent value="events">
              {ready && <OcelEventsTab logId={logId} />}
            </TabsContent>
            <TabsContent value="relationships">
              {ready && <OcelRelationshipsTab logId={logId} />}
            </TabsContent>
          </>
        ) : (
          <>
            <TabsContent value="events">
              {ready && <EventsTab logId={logId} log={log} />}
            </TabsContent>
            <TabsContent value="variants">
              {ready && <VariantsTab logId={logId} log={log} />}
            </TabsContent>
            <TabsContent value="activities">
              {ready && <ActivitiesTab logId={logId} log={log} />}
            </TabsContent>
          </>
        )}
        <TabsContent value="settings">
          <SettingsTab logId={logId} log={log} />
        </TabsContent>
      </PageContainer>
    </Tabs>
  );
}
