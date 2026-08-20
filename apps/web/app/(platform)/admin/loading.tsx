import { StatCardsSkeleton, TableSkeleton } from "@/components/skeletons";

// Renders inside admin/layout.tsx (which owns the container + title + tabs), so
// this only fills the content area below the tab strip while a tab loads.
export default function Loading() {
  return (
    <div className="space-y-4">
      <StatCardsSkeleton />
      <TableSkeleton />
    </div>
  );
}
