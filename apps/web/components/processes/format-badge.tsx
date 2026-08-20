import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/cn";

const FORMAT_STYLES: Record<string, { label: string; className: string }> = {
  xes: {
    label: "XES",
    className: "bg-blue-500/15 text-blue-700 dark:text-blue-300 border-blue-500/20",
  },
  "xes.gz": {
    label: "XES.GZ",
    className: "bg-cyan-500/15 text-cyan-700 dark:text-cyan-300 border-cyan-500/20",
  },
  csv: {
    label: "CSV",
    className:
      "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border-emerald-500/20",
  },
  xml: {
    label: "XML",
    className: "bg-amber-500/15 text-amber-700 dark:text-amber-300 border-amber-500/20",
  },
  ocel: {
    label: "OCEL",
    className:
      "bg-violet-500/15 text-violet-700 dark:text-violet-300 border-violet-500/20",
  },
};

export function FormatBadge({
  format,
  className,
}: {
  format: string | null | undefined;
  className?: string;
}) {
  if (!format) {
    return <span className="text-xs text-muted-foreground">–</span>;
  }
  const style = FORMAT_STYLES[format.toLowerCase()] ?? {
    label: format.toUpperCase(),
    className: "bg-muted text-muted-foreground border-border",
  };
  return (
    <Badge
      variant="outline"
      className={cn("px-1.5 py-0 text-[10px] font-semibold tracking-wide", style.className, className)}
    >
      {style.label}
    </Badge>
  );
}
