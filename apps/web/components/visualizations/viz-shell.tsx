"use client";

/** Centered empty/placeholder state shared by the generic visualizations. The
 * generic-viz card owns loading/error/unconfigured; a viz renders this when its
 * (already-loaded) dataset has nothing to draw. */
export function VizEmpty({ message }: { message?: string }) {
  return (
    <div className="flex h-full w-full items-center justify-center p-4 text-center text-xs text-muted-foreground">
      {message ?? "No data to display."}
    </div>
  );
}
