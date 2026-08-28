import { PageContainer } from "@/components/page";
import { Skeleton } from "@/components/ui/skeleton";

// Dropzone + form-field pairs – the import wizard's rough geometry. The page
// renders no header of its own (the topbar owns the title), so neither does it.
export default function ImportLoading() {
  return (
    <PageContainer>
      <div className="max-w-2xl space-y-6">
        <Skeleton className="h-40 w-full rounded-xl" />
        <div className="space-y-2">
          <Skeleton className="h-3.5 w-24" />
          <Skeleton className="h-9 w-full rounded-md" />
        </div>
        <div className="space-y-2">
          <Skeleton className="h-3.5 w-32" />
          <Skeleton className="h-9 w-full rounded-md" />
        </div>
        <Skeleton className="h-9 w-32 rounded-md" />
      </div>
    </PageContainer>
  );
}
