import { PageContainer } from "@/components/page";
import { Skeleton } from "@/components/ui/skeleton";

// Upload dropzone + field pairs – the module upload page's geometry. The page
// renders no header of its own (the topbar owns the title), so neither does it.
export default function ModuleImportLoading() {
  return (
    <PageContainer>
      <div className="max-w-2xl space-y-6">
        <Skeleton className="h-40 w-full rounded-xl" />
        <div className="space-y-2">
          <Skeleton className="h-3.5 w-28" />
          <Skeleton className="h-9 w-full rounded-md" />
        </div>
        <Skeleton className="h-9 w-32 rounded-md" />
      </div>
    </PageContainer>
  );
}
