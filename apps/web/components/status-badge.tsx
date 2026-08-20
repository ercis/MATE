import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/cn";

type Variant = "neutral" | "info" | "success" | "warning" | "error";

const STYLES: Record<Variant, string> = {
  neutral: "bg-muted text-muted-foreground",
  info: "bg-chart-3/15 text-foreground",
  success: "bg-chart-2/15 text-foreground",
  warning: "bg-chart-4/20 text-foreground",
  error: "bg-destructive/15 text-destructive",
};

const STATUS_VARIANT: Record<string, Variant> = {
  importing: "info",
  ready: "success",
  failed: "error",

  queued: "neutral",
  running: "info",
  paused: "warning",
  completed: "success",
  cancelled: "warning",
};

export function StatusBadge({ status, className }: { status: string; className?: string }) {
  const variant = STATUS_VARIANT[status] ?? "neutral";
  return (
    <Badge
      className={cn(
        "capitalize border-0 font-normal",
        STYLES[variant],
        className,
      )}
      aria-label={`Status: ${status}`}
    >
      {status}
    </Badge>
  );
}
