"use client";

import dynamic from "next/dynamic";
import { useParams } from "next/navigation";
import { Inbox } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { Badge } from "@/components/ui/badge";
import { PageContainer } from "@/components/page";
import { ChartCardSkeleton } from "@/components/skeletons";
import { useEventLog, useVariant, useVariantCases } from "@/lib/queries";
import { displayActivity, getActivityRenameMap } from "@/lib/activity-rename";
import { VariantHeader } from "@/components/processes/variant-detail/header";
import { VariantDetailSkeleton } from "@/components/processes/variant-detail/skeleton";
import { SequenceStrip } from "@/components/processes/variant-detail/sequence-strip";
import { CaseList } from "@/components/processes/variant-detail/case-list";
import { AttributeBreakdowns } from "@/components/processes/variant-detail/attribute-breakdowns";

// recharts lives inside DurationHistogram; load it in an async chunk so the
// variant-detail route's First Load JS stays small. ssr:false because the chart
// only renders client-side from already-fetched variant data.
const DurationHistogram = dynamic(
  () =>
    import("@/components/processes/variant-detail/duration-histogram").then(
      (m) => m.DurationHistogram,
    ),
  { ssr: false, loading: () => <ChartCardSkeleton /> },
);

export default function VariantDetailPage() {
  const params = useParams<{ logId: string; variantId: string }>();
  const { logId, variantId } = params;

  const { data: log } = useEventLog(logId);
  const { data: variant, isLoading, isError, error } = useVariant(logId, variantId);
  const { data: cases } = useVariantCases(logId, variantId);

  if (isLoading) {
    return <VariantDetailSkeleton />;
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
      <header className="space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-sm font-medium text-muted-foreground">#{variant.rank}</span>
          <Badge variant="outline" className="border-0 bg-muted text-[10px] font-mono uppercase tracking-wide text-muted-foreground">
            {variant.variant_id}
          </Badge>
        </div>
        <VariantHeader variant={variant} />
      </header>

      <SequenceStrip
        logId={logId}
        items={variant.activities.map((raw) => ({
          raw,
          label: displayActivity(raw, getActivityRenameMap(log)),
        }))}
      />

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
