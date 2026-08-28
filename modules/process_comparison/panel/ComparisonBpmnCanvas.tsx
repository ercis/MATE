"use client";

// bpmn-js CSS is loaded by the host app (apps/web), not imported here - the
// module bundler (esbuild) has no loaders for the font assets the BPMN font CSS
// references. See apps/web/app/layout.tsx.

import { useEffect, useRef, useState, type ReactNode } from "react";
import { AlertTriangle } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import {
  fitBpmnViewport,
  useCanvasIdleVisibility,
  useFullscreen,
} from "@/components/visualizations/canvases/shared/canvas-controls";
import { CanvasControlCluster } from "@/components/visualizations/canvases/shared/canvas-toolbar";

// bpmn-js / bpmn-auto-layout are bundled straight into this panel - intentionally
// NOT in runtime-externals.json. The host runs `next dev --turbo`, and Turbopack
// mis-bundles bpmn-moddle so its parser stops recognising the `bpmn:Definitions`
// root ("failed to parse document as <bpmn:Definitions>"); esbuild (this panel's
// bundler) handles it correctly. The discovery + conformance modules bundle them
// the same way. Do NOT re-externalise to "share" the host instance.
//
// NavigatedViewer is the strictly view-only bpmn-js entry: pan + scroll-zoom.
import NavigatedViewer from "bpmn-js/lib/NavigatedViewer";
import { layoutProcess } from "bpmn-auto-layout";
// diagram-js-minimap ships JS only (no d.ts); esbuild bundles it like bpmn-js.
// Its CSS is imported by apps/web/app/layout.tsx.
import minimapModule from "diagram-js-minimap";

import {
  applyComparisonOverlay,
  injectComparisonStyles,
  BPMN_THEME_COLORS,
  type ActivityMap,
  type BpmnModelerLike,
} from "./comparison-decorate";

// pm4py BPMN has no BPMNDI (coordinates). bpmn-auto-layout idempotently fills
// them in; files that already have DI pass through unchanged.
async function ensureLayout(xml: string): Promise<string> {
  try {
    return await layoutProcess(xml);
  } catch {
    return xml;
  }
}

export interface ComparisonDecor {
  heatmap: boolean;
  labels: boolean;
}

export interface ComparisonBpmnCanvasProps {
  /** Initial BPMN XML. Updates after mount are ignored - re-mount via a `key`. */
  xml: string;
  map?: ActivityMap;
  decor?: ComparisonDecor;
  /** Bump to re-fit the diagram after an external container resize (e.g. a layout
   *  toggle switching this pane from half- to full-width). Skips the mount run. */
  refitKey?: string | number;
  /** Settings-popover body for the canvas control cluster (see
   *  `canvas-toolbar.tsx`). Every canvas keeps its controls in here. */
  settings?: ReactNode;
}

type ModelerHandle = BpmnModelerLike & {
  destroy: () => void;
  importXML: (xml: string) => Promise<unknown>;
};

const DEFAULT_DECOR: ComparisonDecor = { heatmap: true, labels: true };

export function ComparisonBpmnCanvas({
  xml,
  map,
  decor,
  refitKey,
  settings,
}: ComparisonBpmnCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const modelerRef = useRef<ModelerHandle | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const rootRef = useRef<HTMLDivElement>(null);
  const { isFullscreen, toggle: toggleFullscreen } = useFullscreen(rootRef);
  // Minimap hidden by default; fades in only while the user pans/zooms.
  const { visible: minimapVisible, notifyActivity } = useCanvasIdleVisibility({ idleMs: 1200 });

  useEffect(() => {
    injectComparisonStyles();
    const container = containerRef.current;
    if (!container) return;

    let modeler: ModelerHandle | null = null;
    let cancelled = false;

    (async () => {
      modeler = new NavigatedViewer({
        container,
        additionalModules: [minimapModule],
        bpmnRenderer: BPMN_THEME_COLORS,
      }) as unknown as ModelerHandle;
      try {
        const laidOut = await ensureLayout(xml);
        if (cancelled || !modeler) return;
        await modeler.importXML(laidOut);
        const canvas = modeler.get<{
          zoom: (scale?: number | string, center?: string) => number;
        }>("canvas");
        fitBpmnViewport(canvas);
        // Open the minimap (we drive its show/hide via the `ff-minimap-hidden`
        // opacity class) and pump interaction activity on every viewbox change.
        try {
          modeler.get<{ open: () => void }>("minimap").open();
          modeler
            .get<{ on: (e: string, cb: () => void) => void }>("eventBus")
            .on("canvas.viewbox.changed", () => notifyActivity());
        } catch {
          /* minimap is best-effort – never block the canvas on it */
        }
      } catch (err) {
        console.error("ComparisonBpmnCanvas: importXML failed", err);
        if (!cancelled) setError((err as Error)?.message ?? "Failed to render BPMN");
        return;
      }
      modelerRef.current = modeler;
      setReady(true);
    })();

    return () => {
      cancelled = true;
      modelerRef.current = null;
      setReady(false);
      try {
        modeler?.destroy();
      } catch {
        /* ignore - destroy can throw if import never completed */
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Re-apply the comparison overlay whenever the data or toggles change.
  useEffect(() => {
    const modeler = modelerRef.current;
    if (!ready || !modeler || !map) return;
    const d = decor ?? DEFAULT_DECOR;
    try {
      applyComparisonOverlay(modeler, { map, heatmap: d.heatmap, labels: d.labels });
    } catch (err) {
      console.error("ComparisonBpmnCanvas: applyComparisonOverlay failed", err);
    }
  }, [ready, map, decor]);

  // Re-fit on fullscreen enter/exit (the viewport just resized). Skip mount.
  const didMountFs = useRef(false);
  useEffect(() => {
    if (!didMountFs.current) {
      didMountFs.current = true;
      return;
    }
    const id = requestAnimationFrame(() => {
      const canvas = modelerRef.current?.get<{
        zoom: (scale?: number | string, center?: string) => number;
      }>("canvas");
      if (canvas) fitBpmnViewport(canvas);
    });
    return () => cancelAnimationFrame(id);
  }, [isFullscreen]);

  // Re-fit when an external resize signal changes: the layout toggle grows this
  // pane from half- to full-width, and bpmn-js does not auto-refit on container
  // resize, so without this the diagram keeps its cramped half-width zoom. Skip
  // the mount run — the import path already frames it via `fitBpmnViewport`.
  const didMountRefit = useRef(false);
  useEffect(() => {
    if (!didMountRefit.current) {
      didMountRefit.current = true;
      return;
    }
    const id = requestAnimationFrame(() => {
      const canvas = modelerRef.current?.get<{
        zoom: (scale?: number | string, center?: string) => number;
      }>("canvas");
      if (canvas) fitBpmnViewport(canvas);
    });
    return () => cancelAnimationFrame(id);
  }, [refitKey]);

  // Opacity gate for the minimap – toggled by interaction activity.
  useEffect(() => {
    containerRef.current
      ?.querySelector(".djs-minimap")
      ?.classList.toggle("ff-minimap-hidden", !minimapVisible);
  }, [minimapVisible, ready]);

  const zoomBy = (factor: number) => {
    const modeler = modelerRef.current;
    if (!modeler) return;
    const canvas = modeler.get<{ zoom: (m?: number | string, c?: string) => number }>("canvas");
    const current = canvas.zoom();
    canvas.zoom(typeof current === "number" ? current * factor : "fit-viewport", "auto");
  };
  const fit = () => {
    const canvas = modelerRef.current?.get<{
      zoom: (scale?: number | string, center?: string) => number;
    }>("canvas");
    if (canvas) fitBpmnViewport(canvas);
  };

  if (error) {
    return <EmptyState icon={AlertTriangle} title="Could not render BPMN" description={error} />;
  }

  return (
    <div
      ref={rootRef}
      className={`relative h-full w-full${isFullscreen ? " bg-background" : ""}`}
      onWheelCapture={notifyActivity}
      onPointerDownCapture={notifyActivity}
    >
      <div ref={containerRef} className="h-full w-full" />
      {/* Same cluster as every React-Flow canvas – top-right, minimap owns
          bottom-right. Never hand-roll this pill. */}
      <CanvasControlCluster
        onZoomIn={() => zoomBy(1.2)}
        onZoomOut={() => zoomBy(1 / 1.2)}
        onFit={fit}
        isFullscreen={isFullscreen}
        onToggleFullscreen={toggleFullscreen}
        settings={settings}
      />
    </div>
  );
}
