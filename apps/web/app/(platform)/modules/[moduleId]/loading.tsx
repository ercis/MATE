import { PageContainer } from "@/components/page";
import { Skeleton } from "@/components/ui/skeleton";

export default function Loading() {
  return (
    <PageContainer className="space-y-6">
      <div className="space-y-3">
        <Skeleton className="h-3.5 w-40" />
        <div className="flex items-center gap-3">
          <Skeleton className="h-10 w-10 rounded-lg" />
          <Skeleton className="h-7 w-56" />
        </div>
      </div>
      {Array.from({ length: 3 }).map((_, i) => (
        <div
          key={i}
          className="space-y-3 rounded-xl border border-border bg-card p-5"
        >
          <Skeleton className="h-4 w-40" />
          <Skeleton className="h-3 w-72" />
          <Skeleton className="h-9 w-full max-w-md rounded-md" />
        </div>
      ))}
    </PageContainer>
  );
}
