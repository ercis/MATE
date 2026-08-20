import { PageSkeleton } from "@/components/skeletons";

// Fallback loading shell for any (platform) segment without its own loading.tsx.
export default function Loading() {
  return <PageSkeleton />;
}
