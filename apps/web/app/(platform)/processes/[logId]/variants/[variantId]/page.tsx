"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, Inbox } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { PageContainer, PageTitle } from "@/components/page";
import { useEventLog, useVariant, useVariantCases } from "@/lib/queries";
import { displayActivities, getActivityRenameMap } from "@/lib/activity-rename";
import { VariantHeader } from "@/components/processes/variant-detail/header";
import { SequenceStrip } from "@/components/processes/variant-detail/sequence-strip";
import { DurationHistogram } from "@/components/processes/variant-detail/duration-histogram";
import { CaseList } from "@/components/processes/variant-detail/case-list";
import { AttributeBreakdowns } from "@/components/processes/variant-detail/attribute-breakdowns";

export default function VariantDetailPage() {
  const params = useParams<{ logId: string; variantId: string }>();
  const { logId, variantId } = params;

  const { data: log } = useEventLog(logId);
  const { data: variant, isLoading, isError, error } = useVariant(logId, variantId);
  const { data: cases } = useVariantCases(logId, variantId);

  if (isLoading) {
    return (
      <PageContainer className="space-y-4">
        <Skeleton className="h-6 w-40" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-64 w-full" />
      </PageContainer>
    );
  }

  if (isError || !variant) {
    return (
      <EmptyState
        icon={Inbox}
        title="Variant not found"
        description={(error as Error)?.message ?? "It may have been removed by an edit."}
      />
    );
  }

  return (
    <PageContainer className="space-y-8">
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Link
          href={`/processes/${logId}?tab=variants`}
          className="inline-flex items-center gap-1 hover:text-foreground"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          Back to variants
        </Link>
        {log && (
          <>
            <span>·</span>
            <Link href={`/processes/${logId}`} className="hover:text-foreground">
              {log.name}
            </Link>
          </>
        )}
      </div>

      <header className="space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <PageTitle>Variant #{variant.rank}</PageTitle>
          <Badge variant="outline" className="border-0 bg-muted text-[10px] font-mono uppercase tracking-wide text-muted-foreground">
            {variant.variant_id}
          </Badge>
        </div>
        <VariantHeader variant={variant} />
      </header>

      <SequenceStrip activities={displayActivities(variant.activities, getActivityRenameMap(log))} />

      <div className="grid gap-6 lg:grid-cols-2">
        <div className="rounded-lg border p-4">
          <h2 className="mb-3 text-sm font-medium">Case duration distribution</h2>
          <DurationHistogram
            counts={variant.duration_histogram}
            edges={variant.duration_bin_edges_seconds}
          />
        </div>
        <div className="rounded-lg border p-4">
          <h2 className="mb-3 text-sm font-medium">Top attribute values</h2>
          <AttributeBreakdowns breakdowns={variant.attribute_breakdowns} />
        </div>
      </div>

      <div className="rounded-lg border">
        <div className="border-b px-4 py-3 text-sm font-medium">
          Cases following this variant
        </div>
        <CaseList logId={logId} cases={cases?.rows ?? []} total={cases?.total ?? 0} />
      </div>
    </PageContainer>
  );
}
