import { Skeleton } from "@/components/ui/skeleton";

// Renders inside settings/layout.tsx (which owns the container + tabs), so this
// only fills the content area below the tab strip.
export default function Loading() {
  return (
    <div className="space-y-4">
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
    </div>
  );
}
