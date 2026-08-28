"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Gauge, Inbox, Split } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageContainer } from "@/components/page";
import { useActivityCases, useActivityDetail, useEventLog, useModules } from "@/lib/queries";
import { displayActivity, getActivityRenameMap } from "@/lib/activity-rename";
import { modulePath } from "@/lib/dashboards/drill";
import { ActivityHeader } from "@/components/processes/activity-detail/header";
import { ActivityDetailSkeleton } from "@/components/processes/activity-detail/skeleton";
import { TopVariantsList } from "@/components/processes/activity-detail/variant-list";
import { CaseList } from "@/components/processes/variant-detail/case-list";

// The activity identity travels as `?name=` (raw activity name), not a path
// segment: names are arbitrary strings and proxies may normalize
// percent-escapes inside path segments. `useSearchParams` hands back the
// decoded value; `activityHref` in lib/dashboards/drill.ts is the one place
// that encodes it.
export default function ActivityDetailPage() {
  const { logId } = useParams<{ logId: string }>();
  const name = useSearchParams().get("name");

  const { data: log } = useEventLog(logId);
  const { data: detail, isLoading, isError, error } = useActivityDetail(logId, name);
  const { data: cases } = useActivityCases(logId, name);
  const { data: modules } = useModules(logId);

  if (!name) {
    return (
      <EmptyState
        icon={Inbox}
        title="No activity selected"
        description="Open an activity from the Activities tab of a process."
        primaryAction={
          <Button asChild>
            <Link href={`/processes/${logId}?tab=activities`}>Open activities</Link>
          </Button>
        }
      />
    );
  }

  if (isLoading) {
    return <ActivityDetailSkeleton />;
  }

  if (isError || !detail) {
    return (
      <EmptyState
        icon={Inbox}
        title="Activity not found"
        description={(error as Error)?.message ?? "It may have been renamed by an edit."}
        primaryAction={
          <Button asChild variant="outline">
            <Link href={`/processes/${logId}?tab=activities`}>Open activities</Link>
          </Button>
        }
      />
    );
  }

  const renameMap = getActivityRenameMap(log);
  const label = displayActivity(detail.activity, renameMap);
  const hasPerformance = (modules ?? []).some((m) => m.id === "performance");
  const encoded = encodeURIComponent(detail.activity);

  return (
    <PageContainer className="space-y-8">
      <header className="space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-lg font-semibold">{label}</h1>
          {label !== detail.activity && (
            <Badge
              variant="outline"
              className="border-0 bg-muted font-mono text-[10px] text-muted-foreground"
            >
              {detail.activity}
            </Badge>
          )}
        </div>
        <ActivityHeader detail={detail} />
      </header>

      <div className="flex flex-wrap gap-2">
        <Button asChild variant="outline" size="sm">
          <Link href={`/processes/${logId}?tab=variants&activity=${encoded}`}>
            <Split className="mr-1.5 h-3.5 w-3.5" />
            Show variants with this activity
          </Link>
        </Button>
        {hasPerformance && (
          <Button asChild variant="outline" size="sm">
            <Link href={`${modulePath(logId, "performance")}?activity=${encoded}`}>
              <Gauge className="mr-1.5 h-3.5 w-3.5" />
              Open in Performance
            </Link>
          </Button>
        )}
      </div>

      <div className="rounded-lg border">
        <div className="border-b px-4 py-3 text-sm font-medium">
          Variants containing this activity
        </div>
        <TopVariantsList
          logId={logId}
          variants={detail.top_variants}
          totalContaining={detail.variant_count}
          renameMap={renameMap}
        />
      </div>

      <div className="rounded-lg border">
        <div className="border-b px-4 py-3 text-sm font-medium">Cases with this activity</div>
        <CaseList
          logId={logId}
          cases={cases?.rows ?? []}
          total={cases?.total ?? 0}
          emptyLabel="No cases recorded for this activity yet."
        />
      </div>
    </PageContainer>
  );
}
