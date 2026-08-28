import { CardGridSkeleton, PageSkeleton } from "@/components/skeletons";

// Three actions: Import, Start from template, New dashboard — see
// dashboard-list.tsx. Grid mirrors its isLoading branch (CardGridSkeleton).
export default function Loading() {
  return (
    <PageSkeleton actions={3}>
      <CardGridSkeleton count={6} />
    </PageSkeleton>
  );
}
