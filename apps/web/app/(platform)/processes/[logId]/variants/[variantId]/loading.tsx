import { PageContainer } from "@/components/page";
import { Skeleton } from "@/components/ui/skeleton";

// Mirrors the variant detail layout: breadcrumb + header + sequence strip +
// duration histogram / case-list blocks.
export default function Loading() {
  return (
    <PageContainer className="space-y-6">
      <Skeleton className="h-4 w-48" />
      <Skeleton className="h-24 w-full rounded-xl" />
      <Skeleton className="h-16 w-full rounded-xl" />
      <div className="grid gap-4 lg:grid-cols-2">
        <Skeleton className="h-64 w-full rounded-xl" />
        <Skeleton className="h-64 w-full rounded-xl" />
      </div>
    </PageContainer>
  );
}
