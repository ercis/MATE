import { CardGridSkeleton, PageSkeleton } from "@/components/skeletons";

export default function Loading() {
  return (
    <PageSkeleton>
      <CardGridSkeleton count={9} />
    </PageSkeleton>
  );
}
