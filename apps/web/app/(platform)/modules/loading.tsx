import { PageSkeleton, TableSkeleton } from "@/components/skeletons";

export default function Loading() {
  return (
    <PageSkeleton>
      <TableSkeleton rows={9} />
    </PageSkeleton>
  );
}
