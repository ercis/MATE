"use client";

import { Maximize, Minus, Plus } from "lucide-react";

import { Button } from "@/components/ui/button";

/**
 * Floating zoom cluster for the dashboard canvas (n8n-style, bottom-left):
 * zoom out · current % (click = back to 100%) · zoom in · fit board. Rendered
 * outside the scroll container so it stays put while the board scrolls.
 */
export function CanvasZoomControls({
  zoom,
  min,
  max,
  onZoomIn,
  onZoomOut,
  onReset,
  onFit,
}: {
  zoom: number;
  min: number;
  max: number;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onReset: () => void;
  onFit: () => void;
}) {
  return (
    <div className="absolute bottom-3 left-3 z-10 flex items-center gap-0.5 rounded-lg border border-white/10 [border-top-color:var(--glass-refraction-top)] bg-card/80 p-0.5 shadow-lg backdrop-blur-xl backdrop-saturate-150">
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="h-7 w-7"
        onClick={onZoomOut}
        disabled={zoom <= min}
        aria-label="Zoom out"
        title="Zoom out (Ctrl+scroll)"
      >
        <Minus className="h-3.5 w-3.5" />
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="h-7 min-w-[3.25rem] px-1.5 text-xs font-normal tabular-nums text-muted-foreground hover:text-foreground"
        onClick={onReset}
        aria-label="Reset zoom to 100%"
        title="Reset zoom"
      >
        {Math.round(zoom * 100)}%
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="h-7 w-7"
        onClick={onZoomIn}
        disabled={zoom >= max}
        aria-label="Zoom in"
        title="Zoom in (Ctrl+scroll)"
      >
        <Plus className="h-3.5 w-3.5" />
      </Button>
      <div className="mx-0.5 h-4 w-px bg-border" aria-hidden />
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="h-7 w-7"
        onClick={onFit}
        aria-label="Fit board to view"
        title="Fit board"
      >
        <Maximize className="h-3.5 w-3.5" />
      </Button>
    </div>
  );
}
