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

// bpmn-js / bpmn-auto-layout are bundled straight into this panel - they are
// intentionally NOT in runtime-externals.json. The host runs `next dev --turbo`,
// and Turbopack mis-bundles bpmn-moddle so its parser stops recognising the
// `bpmn:Definitions` root: every importXML/layoutProcess then fails with
// "failed to parse document as <bpmn:Definitions>" on valid BPMN. esbuild (this
// panel's bundler) handles bpmn-moddle correctly, so we bundle here and never
// touch the host copy. Do NOT re-externalise these to "share" the host instance.
//
// NavigatedViewer is the strictly view-only bpmn-js entry: pan + scroll-zoom,
// NO editing services.
import NavigatedViewer from "bpmn-js/lib/NavigatedViewer";
import { layoutProcess } from "bpmn-auto-layout";
// diagram-js-minimap ships JS only (no d.ts); esbuild bundles it like bpmn-js.
// Its CSS is imported by apps/web/app/layout.tsx.
import minimapModule from "diagram-js-minimap";

import {
  applyConformanceOverlay,
  injectConformanceStyles,
  locateActivity,
  BPMN_THEME_COLORS,
  type BpmnModelerLike,
  type DeviationMaps,
} from "../conformance-decorate";

// An uploaded BPMN may or may not carry BPMNDI (coordinates). bpmn-auto-layout
// idempotently fills them in; files that already have DI pass through unchanged.
async function ensureLayout(xml: string): Promise<string> {
  try {
    return await layoutProcess(xml);
  } catch {
    return xml;
  }
}

export interface ConformanceDecor {
  heatmap: boolean;
  labels: boolean;
  alignments: boolean;
}

export interface ConformanceBpmnCanvasProps {
  /** Initial BPMN XML. Updates after mount are ignored - re-mount via a `key`. */
  xml: string;
  maps?: DeviationMaps;
  decor?: ConformanceDecor;
  searchQuery?: string;
  searchNonce?: number;
  onSearchResult?: (found: boolean) => void;
  /** Settings-popover body for the canvas control cluster (see
   *  `canvas-toolbar.tsx`). Every canvas keeps its controls in here. */
  settings?: ReactNode;
}

type ModelerHandle = BpmnModelerLike & {
  destroy: () => void;
  importXML: (xml: string) => Promise<unknown>;
};

const DEFAULT_DECOR: ConformanceDecor = { heatmap: true, labels: true, alignments: false };

export function ConformanceBpmnCanvas({
  xml,
  maps,
  decor,
  searchQuery,
  searchNonce,
  onSearchResult,
  settings,
}: ConformanceBpmnCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const modelerRef = useRef<ModelerHandle | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const onSearchResultRef = useRef(onSearchResult);
  onSearchResultRef.current = onSearchResult;

  const rootRef = useRef<HTMLDivElement>(null);
  const { isFullscreen, toggle: toggleFullscreen } = useFullscreen(rootRef);
  // Minimap hidden by default; fades in only while the user pans/zooms.
  const { visible: minimapVisible, notifyActivity } = useCanvasIdleVisibility({ idleMs: 1200 });

  useEffect(() => {
    injectConformanceStyles();
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
        // Open the minimap (visibility driven via the `ff-minimap-hidden`
        // opacity class) and pump interaction activity on viewbox change.
        try {
          modeler.get<{ open: () => void }>("minimap").open();
          modeler
            .get<{ on: (e: string, cb: () => void) => void }>("eventBus")
            .on("canvas.viewbox.changed", () => notifyActivity());
        } catch {
          /* minimap is best-effort – never block the canvas on it */
        }
      } catch (err) {
        console.error("ConformanceBpmnCanvas: importXML failed", err);
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

  // Re-apply the deviation overlay whenever the data or toggles change.
  useEffect(() => {
    const modeler = modelerRef.current;
    if (!ready || !modeler || !maps) return;
    const d = decor ?? DEFAULT_DECOR;
    try {
      applyConformanceOverlay(modeler, {
        maps,
        heatmap: d.heatmap,
        labels: d.labels,
        alignments: d.alignments,
      });
    } catch (err) {
      console.error("ConformanceBpmnCanvas: applyConformanceOverlay failed", err);
    }
  }, [ready, maps, decor]);

  useEffect(() => {
    const modeler = modelerRef.current;
    if (!ready || !modeler || !searchNonce || !searchQuery) return;
    const found = locateActivity(modeler, searchQuery);
    onSearchResultRef.current?.(found);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchNonce]);

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
