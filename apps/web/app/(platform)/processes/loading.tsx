import { PageSkeleton } from "@/components/skeletons";

// The header carries the model filter tabs plus three actions (watched folders,
// new folder, import) - see processes-client.tsx.
export default function Loading() {
  return <PageSkeleton withFilters actions={3} />;
}
