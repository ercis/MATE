"use client";

// bpmn-js CSS is loaded by the host app (apps/web) rather than imported here
// – the module bundler (esbuild) has no loaders for the .woff/.ttf/.eot/.svg
// font assets the BPMN font CSS references.
// See apps/web/app/layout.tsx for the actual imports.

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { AlertTriangle } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { formatNumber } from "@/lib/format";
import {
  useCanvasIdleVisibility,
  useFullscreen,
  useSmoothBpmnZoom,
  type BpmnZoomCanvas,
} from "@/components/visualizations/canvases/shared/canvas-controls";
import {
  CanvasBusyChip,
  CanvasControlCluster,
} from "@/components/visualizations/canvases/shared/canvas-toolbar";

// bpmn-js / bpmn-auto-layout are bundled straight into this panel – they are
// intentionally NOT in runtime-externals.json. The host runs `next dev --turbo`,
// and Turbopack mis-bundles bpmn-moddle so its parser stops recognising the
// `bpmn:Definitions` root: every importXML/layoutProcess then fails with
// "failed to parse document as <bpmn:Definitions>" on valid BPMN. esbuild (this
// panel's bundler) handles bpmn-moddle correctly, so we bundle here and never
// touch the host copy. Do NOT re-externalise these to "share" the host instance
// – that reintroduces the parse failure.
//
// NavigatedViewer is the strictly view-only bpmn-js entry: pan + scroll-zoom +
// keyboard-pan, and NO editing services (no palette / context-pad /
// direct-editing / create / connect / move / resize / bendpoints / label-edit).
import NavigatedViewer from "bpmn-js/lib/NavigatedViewer";
import { layoutProcess } from "bpmn-auto-layout";
// diagram-js-minimap ships JS only (no d.ts); esbuild bundles it like bpmn-js.
// Its CSS is imported by apps/web/app/layout.tsx.
import minimapModule from "diagram-js-minimap";

import {
  applyBpmnOverlay,
  injectBpmnStyles,
  locateActivity,
  BPMN_THEME_COLORS,
  type BpmnModelerLike,
  type FrequencyMaps,
} from "../bpmn-decorate";

// pm4py emits BPMN XML without BPMNDI (no coordinates). bpmn-auto-layout
// idempotently fills them in. Hand-authored / Camunda files already have DI
// and pass through unchanged.
async function ensureLayout(xml: string): Promise<string> {
  try {
    return await layoutProcess(xml);
  } catch {
    return xml;
  }
}

export interface BpmnDecor {
  heatmap: boolean;
  freqLabels: boolean;
}

export interface BpmnCanvasProps {
  /** BPMN XML. Re-imported in place when it changes (a re-mine from the
   *  frequency filter) so the canvas – and the settings popover the user just
   *  dragged a slider in – stays mounted. */
  xml: string;
  /** DFG-derived frequency maps driving the heatmap / badges. */
  freq?: FrequencyMaps;
  /** Frequency-overlay display toggles. */
  decor?: BpmnDecor;
  /** Activity search term; centred + highlighted whenever `searchNonce` bumps. */
  searchQuery?: string;
  searchNonce?: number;
  /** Reports whether the last search located an activity. */
  onSearchResult?: (found: boolean) => void;
  /** Settings-popover body for the canvas control cluster (see
   *  `canvas-toolbar.tsx`). Every canvas keeps its controls in here. */
  settings?: ReactNode;
  /** Cluster reset. Restores whatever view state the *owner* holds (this view's
   *  frequency filter + overlay toggles); the canvas re-fits on top. Omit and
   *  the button doesn't render. */
  onReset?: () => void;
  /** A re-mine is in flight while the previous model stays on screen. */
  busy?: boolean;
}

type ModelerHandle = BpmnModelerLike & {
  destroy: () => void;
  importXML: (xml: string) => Promise<unknown>;
};

/** Overlay defaults – also what the cluster's reset restores the tab to, so
 *  keep `BpmnTab`'s initial state reading from here rather than re-literalling. */
export const DEFAULT_BPMN_DECOR: BpmnDecor = {
  heatmap: true,
  freqLabels: true,
};

export function BpmnCanvas({
  xml,
  freq,
  decor,
  searchQuery,
  searchNonce,
  onSearchResult,
  settings,
  onReset,
  busy = false,
}: BpmnCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const modelerRef = useRef<ModelerHandle | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Bumped once the viewer instance exists so the import effect can run.
  const [viewerNonce, setViewerNonce] = useState(0);
  // Bumped after every successful import – a fresh diagram has no overlay
  // decorations, so the frequency overlay effect has to run again.
  const [importNonce, setImportNonce] = useState(0);
  // The minimap is opened / wired exactly once per viewer, not per import.
  const minimapWired = useRef(false);
  // Capture the handler in a ref so the mount effect can stay [] without
  // re-creating the viewer when the parent's callback identity changes.
  const onSearchResultRef = useRef(onSearchResult);
  onSearchResultRef.current = onSearchResult;

  const rootRef = useRef<HTMLDivElement>(null);
  const { isFullscreen, toggle: toggleFullscreen } = useFullscreen(rootRef);
  // Minimap hidden by default; fades in only while the user pans/zooms.
  const { visible: minimapVisible, notifyActivity } = useCanvasIdleVisibility({ idleMs: 1200 });
  // Tweened buttons + continuous cursor-anchored pinch zoom, replacing
  // diagram-js's snap-to-step zoom. Reads the canvas service through a getter so
  // it survives a viewer swap; `notifyActivity` keeps the minimap up while zooming.
  const getZoomCanvas = useCallback(
    () => modelerRef.current?.get<BpmnZoomCanvas>("canvas") ?? null,
    [],
  );
  const { zoomIn, zoomOut, fit, fitNow } = useSmoothBpmnZoom(getZoomCanvas, containerRef, {
    onActivity: notifyActivity,
  });

  // Create the viewer once. Importing is a separate effect so a new `xml`
  // (re-mine) swaps the model in place instead of tearing the canvas – and its
  // control cluster – down.
  useEffect(() => {
    injectBpmnStyles();
    const container = containerRef.current;
    if (!container) return;

    const modeler = new NavigatedViewer({
      container,
      additionalModules: [minimapModule],
      bpmnRenderer: BPMN_THEME_COLORS,
    }) as unknown as ModelerHandle;
    modelerRef.current = modeler;
    minimapWired.current = false;
    setViewerNonce((n) => n + 1);

    return () => {
      modelerRef.current = null;
      setReady(false);
      try {
        modeler.destroy();
      } catch {
        /* ignore – destroy can throw if import never completed */
      }
    };
  }, []);

  useEffect(() => {
    const modeler = modelerRef.current;
    if (!modeler || viewerNonce === 0) return;

    let cancelled = false;
    (async () => {
      try {
        const laidOut = await ensureLayout(xml);
        if (cancelled || modelerRef.current !== modeler) return;
        await modeler.importXML(laidOut);
        if (cancelled || modelerRef.current !== modeler) return;
        // Instant, not tweened: the diagram should appear already framed.
        fitNow();
        // Open the minimap (we drive its show/hide via the `ff-minimap-hidden`
        // opacity class) and pump interaction activity on every viewbox change.
        // Once per viewer – a re-import keeps the same eventBus, so re-wiring
        // would stack duplicate listeners.
        if (!minimapWired.current) {
          minimapWired.current = true;
          try {
            modeler.get<{ open: () => void }>("minimap").open();
            modeler
              .get<{ on: (e: string, cb: () => void) => void }>("eventBus")
              .on("canvas.viewbox.changed", () => notifyActivity());
          } catch {
            /* minimap is best-effort – never block the canvas on it */
          }
        }
      } catch (err) {
        // A blank canvas with no signal hid genuine import failures. Surface
        // them in an empty state instead of only logging to the console.
        console.error("BpmnCanvas: importXML failed", err);
        if (!cancelled) setError((err as Error)?.message ?? "Failed to render BPMN");
        return;
      }
      if (!cancelled) {
        setError(null);
        setReady(true);
        setImportNonce((n) => n + 1);
      }
    })();

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewerNonce, xml]);

  // Re-apply the frequency overlay whenever the data or toggles change.
  useEffect(() => {
    const modeler = modelerRef.current;
    if (!ready || !modeler || !freq) return;
    const d = decor ?? DEFAULT_BPMN_DECOR;
    try {
      applyBpmnOverlay(modeler, {
        freq,
        heatmap: d.heatmap,
        freqLabels: d.freqLabels,
        formatNumber,
      });
    } catch (err) {
      console.error("BpmnCanvas: applyBpmnOverlay failed", err);
    }
  }, [ready, importNonce, freq, decor]);

  // Locate an activity on demand.
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
    const id = requestAnimationFrame(() => fitNow());
    return () => cancelAnimationFrame(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isFullscreen]);

  // Opacity gate for the minimap – toggled by interaction activity.
  useEffect(() => {
    containerRef.current
      ?.querySelector(".djs-minimap")
      ?.classList.toggle("ff-minimap-hidden", !minimapVisible);
  }, [minimapVisible, ready]);

  if (error) {
    return (
      <EmptyState
        icon={AlertTriangle}
        title="Could not render BPMN"
        description={error}
      />
    );
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
        onZoomIn={zoomIn}
        onZoomOut={zoomOut}
        onFit={fit}
        isFullscreen={isFullscreen}
        onToggleFullscreen={toggleFullscreen}
        settings={settings}
        // The owner restores its own view state; re-framing is ours. A non-zero
        // frequency filter re-mines, which re-imports and re-fits anyway – the
        // fit here is what makes the button do something when it's already 0.
        onReset={
          onReset
            ? () => {
                onReset();
                fit();
              }
            : undefined
        }
        resetLabel="Reset view"
        resetTitle="Reset BPMN view?"
        resetDescription="The frequency filter and the overlay toggles go back to their defaults and the diagram is re-framed. Clearing a non-zero filter re-mines the model."
      />
      {busy && <CanvasBusyChip label="Re-mining…" />}
    </div>
  );
}
