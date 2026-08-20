"use client";

// bpmn-js CSS is loaded by the host app (apps/web) rather than imported here
// – the module bundler (esbuild) has no loaders for the .woff/.ttf/.eot/.svg
// font assets the BPMN font CSS references.
// See apps/web/app/layout.tsx for the actual imports.

import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Maximize, Minus, Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/empty-state";
import { formatNumber } from "@/lib/format";

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

import {
  applyBpmnOverlay,
  injectBpmnStyles,
  locateActivity,
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
  /** Initial BPMN XML. Updates after mount are ignored – re-mount the
   *  component via a `key` prop to swap models. */
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
}

type ModelerHandle = BpmnModelerLike & {
  destroy: () => void;
  importXML: (xml: string) => Promise<unknown>;
};

const DEFAULT_DECOR: BpmnDecor = {
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
}: BpmnCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const modelerRef = useRef<ModelerHandle | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Capture the handler in a ref so the mount effect can stay [] without
  // re-creating the viewer when the parent's callback identity changes.
  const onSearchResultRef = useRef(onSearchResult);
  onSearchResultRef.current = onSearchResult;

  useEffect(() => {
    injectBpmnStyles();
    const container = containerRef.current;
    if (!container) return;

    let modeler: ModelerHandle | null = null;
    let cancelled = false;

    (async () => {
      modeler = new NavigatedViewer({
        container,
      }) as unknown as ModelerHandle;

      try {
        const laidOut = await ensureLayout(xml);
        if (cancelled || !modeler) return;
        await modeler.importXML(laidOut);
        const canvas = modeler.get<{ zoom: (mode: string) => void }>("canvas");
        canvas.zoom("fit-viewport");
      } catch (err) {
        // A blank canvas with no signal hid genuine import failures. Surface
        // them in an empty state instead of only logging to the console.
        console.error("BpmnCanvas: importXML failed", err);
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
        /* ignore – destroy can throw if import never completed */
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Re-apply the frequency overlay whenever the data or toggles change.
  useEffect(() => {
    const modeler = modelerRef.current;
    if (!ready || !modeler || !freq) return;
    const d = decor ?? DEFAULT_DECOR;
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
  }, [ready, freq, decor]);

  // Locate an activity on demand.
  useEffect(() => {
    const modeler = modelerRef.current;
    if (!ready || !modeler || !searchNonce || !searchQuery) return;
    const found = locateActivity(modeler, searchQuery);
    onSearchResultRef.current?.(found);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchNonce]);

  const zoomBy = (factor: number) => {
    const modeler = modelerRef.current;
    if (!modeler) return;
    const canvas = modeler.get<{ zoom: (m?: number | string) => number }>("canvas");
    const current = canvas.zoom();
    canvas.zoom(typeof current === "number" ? current * factor : "fit-viewport");
  };
  const fit = () =>
    modelerRef.current?.get<{ zoom: (m: string) => void }>("canvas").zoom("fit-viewport");

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
    <div className="relative h-full w-full">
      <div ref={containerRef} className="h-full w-full" />
      <div className="absolute bottom-3 right-3 z-10 flex flex-col gap-1 rounded-md border bg-card/90 p-1 shadow-sm backdrop-blur">
        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => zoomBy(1.2)} title="Zoom in">
          <Plus className="h-4 w-4" />
        </Button>
        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => zoomBy(1 / 1.2)} title="Zoom out">
          <Minus className="h-4 w-4" />
        </Button>
        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={fit} title="Fit to view">
          <Maximize className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}
