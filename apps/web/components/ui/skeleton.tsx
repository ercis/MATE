import { cn } from "@/lib/cn"

function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="skeleton"
      // skeleton-shimmer (globals.css) adds a translating gradient sweep on
      // top of the pulse. This component is a runtime external, so module
      // panel skeletons pick the upgrade up without rebundling.
      className={cn("skeleton-shimmer animate-pulse rounded-md bg-accent", className)}
      {...props}
    />
  )
}

export { Skeleton }
