"use client";

import { useCallback, useEffect, useRef, type ReactNode } from "react";
import {
  Background,
  BackgroundVariant,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  SelectionMode,
  useNodesInitialized,
  useReactFlow,
  type Edge,
  type EdgeMouseHandler,
  type EdgeTypes,
  type Node,
  type NodeMouseHandler,
  type NodeTypes,
  type OnEdgesChange,
  type OnNodeDrag,
  type OnNodesChange,
  type ReactFlowProps,
} from "@xyflow/react";

import "@xyflow/react/dist/style.css";

import { cn } from "@/lib/cn";
import { useCanvasIdleVisibility, useFullscreen } from "./canvas-controls";
import {
  CanvasBusyChip,
  CanvasControlCluster,
  type CanvasResetSlotProps,
  type CanvasSettingsSlotProps,
} from "./canvas-toolbar";

interface CanvasShellProps extends CanvasSettingsSlotProps, CanvasResetSlotProps {
  nodes: Node[];
  edges: Edge[];
  nodeTypes?: NodeTypes;
  edgeTypes?: EdgeTypes;
  fitViewKey?: string | number;
  className?: string;
  miniMap?: boolean;
  showGrid?: boolean;
  /** A refetch/re-layout is in flight while the current graph stays mounted →
   *  a small "Working…" chip instead of unmounting the canvas (which would
   *  close the settings popover the user just changed something in). */
  busy?: boolean;
  /** Rare canvas-specific toolbar buttons, appended after settings + reset and
   *  before the fullscreen toggle (which always stays rightmost). */
  toolbarSlot?: ReactNode;
  /** Optional content rendered as an absolute-positioned overlay on top of
   *  the canvas – used for the click-to-inspect details panel. */
  overlay?: ReactNode;
  proOptions?: ReactFlowProps["proOptions"];
  onNodeClick?: NodeMouseHandler;
  /** Right-click on a node – e.g. to open a canvas context menu. The handler
   *  is responsible for `event.preventDefault()` to suppress the browser menu. */
  onNodeContextMenu?: NodeMouseHandler;
  onEdgeClick?: EdgeMouseHandler;
  onPaneClick?: (event: React.MouseEvent) => void;
  // The canvas component (DfgCanvas, etc.) parameterises its own state with
  // a specific `Node<TData>` – the shell doesn't need to know which, so we
  // accept the generic-erased forms.
  onNodesChange?: OnNodesChange<Node>;
  onEdgesChange?: OnEdgesChange<Edge>;
  onNodeDragStop?: OnNodeDrag<Node>;
  /** Enable rubber-band (drag-to-select) on the canvas pane. When true,
   *  left-drag creates a selection rectangle; panning moves to middle/right drag. */
  selectionOnDrag?: boolean;
  /** Override pan-on-drag buttons. Defaults to `true` (left button).
   *  Pass `[1, 2]` to restrict panning to middle + right buttons. */
  panOnDrag?: boolean | number[];
  onSelectionChange?: (params: { nodes: Node[]; edges: Edge[] }) => void;
}

// All minimap nodes use a single neutral grey so the minimap reads as a
// structural overview rather than adding a second colour legend.
// Pre-resolved RGB – CSS vars and oklch() in SVG presentation attributes are
// inconsistent across browsers.
const minimapNodeColor = (_node: Node): string => "rgb(148, 163, 184)"; // slate-400

/** Movement below this (px, canvas space) is a click, not a drag – see below. */
const DRAG_PERSIST_THRESHOLD_PX = 2;

function CanvasInner({
  nodes,
  edges,
  nodeTypes,
  edgeTypes,
  fitViewKey,
  miniMap = true,
  showGrid = true,
  busy = false,
  toolbarSlot,
  overlay,
  proOptions,
  onNodeClick,
  onNodeContextMenu,
  onEdgeClick,
  onPaneClick,
  onNodesChange,
  onEdgesChange,
  onNodeDragStop,
  selectionOnDrag = false,
  panOnDrag = true,
  onSelectionChange,
  settings,
  settingsLabel,
  settingsClassName,
  onReset,
  resetLabel,
  resetTitle,
  resetDescription,
  resetConfirmLabel,
  isFullscreen,
  onToggleFullscreen,
}: Omit<CanvasShellProps, "className"> & {
  isFullscreen: boolean;
  onToggleFullscreen: () => void;
}) {
  const { fitView, zoomIn, zoomOut } = useReactFlow();
  const lastKey = useRef<string | number | undefined>(undefined);

  // Minimap is hidden by default and fades in only while the user actively
  // pans/zooms/drags (see the activity handlers below), auto-hiding on idle.
  const { visible: minimapVisible, notifyActivity } = useCanvasIdleVisibility({ idleMs: 1200 });

  useEffect(() => {
    if (fitViewKey !== lastKey.current) {
      lastKey.current = fitViewKey;
      // Defer to next paint so dagre positions are committed first.
      const id = requestAnimationFrame(() => fitView({ duration: 200, padding: 0.2 }));
      return () => cancelAnimationFrame(id);
    }
  }, [fitViewKey, fitView]);

  // Exact initial framing. The `fitView` prop (and any mount-time rAF) can run
  // before React Flow's ResizeObserver has measured node dimensions — the fit
  // then uses zero/stale heights and leaves the graph mis-framed. Re-fit once
  // when all nodes report measured; gated to a single run so later node
  // additions (e.g. filter sliders) never yank the user's viewport.
  const nodesInitialized = useNodesInitialized();
  const didInitFit = useRef(false);
  useEffect(() => {
    if (!nodesInitialized || didInitFit.current) return;
    didInitFit.current = true;
    const id = requestAnimationFrame(() => fitView({ padding: 0.2 }));
    return () => cancelAnimationFrame(id);
  }, [nodesInitialized, fitView]);

  // Re-fit when entering/leaving fullscreen (the viewport just resized). Skip
  // the initial run – the `fitView` prop already frames the graph on mount.
  const didMountFs = useRef(false);
  useEffect(() => {
    if (!didMountFs.current) {
      didMountFs.current = true;
      return;
    }
    const id = requestAnimationFrame(() => fitView({ duration: 200, padding: 0.2 }));
    return () => cancelAnimationFrame(id);
  }, [isFullscreen, fitView]);

  const onFit = useCallback(() => fitView({ duration: 200, padding: 0.2 }), [fitView]);

  // Persist-on-drag guard, applied for every canvas. A plain click can end as a
  // zero-distance "drag" (React Flow still fires onNodeDragStop), and a canvas
  // that persists dragged positions would freeze the node wherever it happened
  // to sit – including mid-layout-animation. Only forward drags that actually
  // moved the node; belt to `nodeDragThreshold` below.
  const dragStartPos = useRef<{ x: number; y: number } | null>(null);
  const handleNodeDragStart = useCallback<OnNodeDrag<Node>>((_, node) => {
    dragStartPos.current = { x: node.position.x, y: node.position.y };
  }, []);
  const handleNodeDragStop = useCallback<OnNodeDrag<Node>>(
    (event, node, dragged) => {
      const from = dragStartPos.current;
      dragStartPos.current = null;
      if (
        from &&
        Math.abs(from.x - node.position.x) <= DRAG_PERSIST_THRESHOLD_PX &&
        Math.abs(from.y - node.position.y) <= DRAG_PERSIST_THRESHOLD_PX
      ) {
        return;
      }
      onNodeDragStop?.(event, node, dragged);
    },
    [onNodeDragStop],
  );

  return (
    // Fade the whole canvas in once it mounts with data – charts/graphs land
    // softly instead of popping. Opacity-only, so xyflow's own layout math is
    // unaffected; runtime external => module canvases inherit this free.
    <div
      className="relative h-full w-full animate-in fade-in-0 duration-300"
      // Wheel-zoom and pointer-pan are extra activity signals that RF's
      // onMove* may not cover on every browser; capture-phase so RF can't
      // swallow them first.
      onWheelCapture={notifyActivity}
      onPointerDownCapture={notifyActivity}
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        proOptions={proOptions ?? { hideAttribution: true }}
        onMoveStart={notifyActivity}
        onMove={notifyActivity}
        onNodeDrag={notifyActivity}
        nodesDraggable
        // Without a threshold a plain CLICK counts as a drag (React Flow
        // default 0) and fires onNodeDragStop — canvases that persist dragged
        // positions then freeze a node wherever it happened to be, including
        // mid-layout-animation. Require real movement before drag semantics.
        nodeDragThreshold={2}
        nodesConnectable={false}
        nodesFocusable
        edgesFocusable
        elementsSelectable
        selectNodesOnDrag={false}
        selectionOnDrag={selectionOnDrag}
        selectionMode={SelectionMode.Partial}
        panOnDrag={panOnDrag}
        zoomOnScroll
        panOnScroll
        fitView
        minZoom={0.1}
        maxZoom={2}
        onNodeClick={onNodeClick}
        onNodeContextMenu={onNodeContextMenu}
        onEdgeClick={onEdgeClick}
        onPaneClick={onPaneClick}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeDragStart={handleNodeDragStart}
        onNodeDragStop={handleNodeDragStop}
        onSelectionChange={onSelectionChange}
        defaultEdgeOptions={{
          interactionWidth: 24,
          focusable: true,
          selectable: true,
        }}
      >
        {showGrid && (
          <Background variant={BackgroundVariant.Dots} gap={20} size={1} className="!bg-background" />
        )}
        {miniMap && (
          // `miniMap` is the enable flag (store gate via `general.showMinimap`);
          // opacity is the interaction gate. `<MiniMap>` positions itself
          // absolutely, so the static wrapper only fades the subtree.
          <div
            className={cn(
              "transition-opacity duration-300",
              minimapVisible ? "opacity-100" : "pointer-events-none opacity-0",
            )}
          >
            <MiniMap
              pannable
              zoomable
              className="!border !border-border !bg-card !rounded-md overflow-hidden shadow-sm"
              maskColor="rgba(0, 0, 0, 0.15)"
              maskStrokeColor="rgba(0, 0, 0, 0.4)"
              maskStrokeWidth={1}
              nodeColor={minimapNodeColor}
              nodeStrokeColor="rgba(0, 0, 0, 0.6)"
              nodeStrokeWidth={2}
              nodeBorderRadius={3}
              offsetScale={4}
            />
          </div>
        )}
      </ReactFlow>

      <CanvasControlCluster
        onZoomIn={() => zoomIn({ duration: 150 })}
        onZoomOut={() => zoomOut({ duration: 150 })}
        onFit={onFit}
        isFullscreen={isFullscreen}
        onToggleFullscreen={onToggleFullscreen}
        settings={settings}
        settingsLabel={settingsLabel}
        settingsClassName={settingsClassName}
        onReset={onReset}
        resetLabel={resetLabel}
        resetTitle={resetTitle}
        resetDescription={resetDescription}
        resetConfirmLabel={resetConfirmLabel}
        extra={toolbarSlot}
      />

      {busy && <CanvasBusyChip />}

      {overlay}
    </div>
  );
}

export function CanvasShell(props: CanvasShellProps) {
  // The sized outer div is the fullscreen target, so the toolbar + minimap go
  // fullscreen with the canvas.
  const containerRef = useRef<HTMLDivElement>(null);
  const { isFullscreen, toggle } = useFullscreen(containerRef);
  return (
    <div
      ref={containerRef}
      className={props.className ?? "h-[640px] w-full overflow-hidden rounded-xl border bg-card"}
    >
      <ReactFlowProvider>
        <CanvasInner {...props} isFullscreen={isFullscreen} onToggleFullscreen={toggle} />
      </ReactFlowProvider>
    </div>
  );
}
