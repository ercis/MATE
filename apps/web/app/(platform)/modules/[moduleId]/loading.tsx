import { PageContainer } from "@/components/page";
import { Skeleton } from "@/components/ui/skeleton";

// Stacked settings cards - module-detail-client.tsx renders no header of its
// own (the topbar owns the name and the ⓘ "About" payload).
export default function Loading() {
  return (
    <PageContainer className="space-y-4">
      {Array.from({ length: 3 }).map((_, i) => (
        <div
          key={i}
          className="space-y-3 rounded-xl border border-white/10 bg-card/60 backdrop-blur-sm p-5"
        >
          <Skeleton className="h-4 w-40" />
          <Skeleton className="h-3 w-72" />
          <Skeleton className="h-9 w-full max-w-md rounded-md" />
        </div>
      ))}
    </PageContainer>
  );
}
